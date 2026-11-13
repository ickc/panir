//! The fuzzer's Rust driver (see fuzz/README.md): tasks on stdin, two lines
//! each (a filter spec or `null`, then a document as pandoc wrote it); for
//! each, one line on stdout: the document after the spec's filters, as JSON,
//! or `!` and the error.

use panir::{
    apply, from_str, to_string, Block, Ctx, Filter, Inline, Meta, MetaValue, Order, Pandoc,
    Topdown, Typewise,
};
use serde::Deserialize;
use std::collections::BTreeMap;
use std::io::{Read, Write};
use std::marker::PhantomData;
use std::mem::take;

#[derive(Deserialize, Clone)]
struct Rule {
    #[serde(rename = "do")]
    act: String,
    #[serde(rename = "mod")]
    modulus: Option<usize>,
    eq: Option<usize>,
}

#[derive(Deserialize)]
struct FilterSpec {
    traverse: String,
    rules: BTreeMap<String, Rule>,
}

#[derive(Deserialize)]
struct Spec {
    filters: Vec<FilterSpec>,
    log: bool,
}

/// One of a spec's filters, in the order `O`; `log` is every call so far.
struct Interp<'a, O> {
    rules: &'a BTreeMap<String, Rule>,
    log: &'a mut Vec<String>,
    order: PhantomData<O>,
}

fn inline_tag(x: &Inline) -> &'static str {
    match x {
        Inline::Str(_) => "Str",
        Inline::Emph(_) => "Emph",
        Inline::Underline(_) => "Underline",
        Inline::Strong(_) => "Strong",
        Inline::Strikeout(_) => "Strikeout",
        Inline::Superscript(_) => "Superscript",
        Inline::Subscript(_) => "Subscript",
        Inline::SmallCaps(_) => "SmallCaps",
        Inline::Quoted(_) => "Quoted",
        Inline::Cite(_) => "Cite",
        Inline::Code(_) => "Code",
        Inline::Space => "Space",
        Inline::SoftBreak => "SoftBreak",
        Inline::LineBreak => "LineBreak",
        Inline::Math(_) => "Math",
        Inline::RawInline(_) => "RawInline",
        Inline::Link(_) => "Link",
        Inline::Image(_) => "Image",
        Inline::Note(_) => "Note",
        Inline::Span(_) => "Span",
    }
}

fn block_tag(x: &Block) -> &'static str {
    match x {
        Block::Plain(_) => "Plain",
        Block::Para(_) => "Para",
        Block::LineBlock(_) => "LineBlock",
        Block::CodeBlock(_) => "CodeBlock",
        Block::RawBlock(_) => "RawBlock",
        Block::BlockQuote(_) => "BlockQuote",
        Block::OrderedList(_) => "OrderedList",
        Block::BulletList(_) => "BulletList",
        Block::DefinitionList(_) => "DefinitionList",
        Block::Header(_) => "Header",
        Block::HorizontalRule => "HorizontalRule",
        Block::Table(_) => "Table",
        Block::Figure(_) => "Figure",
        Block::Div(_) => "Div",
    }
}

fn inline_text(x: &mut Inline) -> Option<&mut panir::Text> {
    match x {
        Inline::Str(s) => Some(s),
        Inline::Code(c) => Some(&mut c.text),
        Inline::Math(m) => Some(&mut m.text),
        Inline::RawInline(r) => Some(&mut r.text),
        _ => None,
    }
}

fn block_text(x: &mut Block) -> Option<&mut panir::Text> {
    match x {
        Block::CodeBlock(c) => Some(&mut c.text),
        Block::RawBlock(r) => Some(&mut r.text),
        _ => None,
    }
}

fn inline_content(x: &mut Inline) -> Option<&mut Vec<Inline>> {
    match x {
        Inline::Emph(c)
        | Inline::Underline(c)
        | Inline::Strong(c)
        | Inline::Strikeout(c)
        | Inline::Superscript(c)
        | Inline::Subscript(c)
        | Inline::SmallCaps(c) => Some(c),
        Inline::Quoted(q) => Some(&mut q.content),
        Inline::Cite(c) => Some(&mut c.content),
        Inline::Link(l) => Some(&mut l.content),
        Inline::Image(i) => Some(&mut i.content),
        Inline::Span(s) => Some(&mut s.content),
        _ => None,
    }
}

fn block_content(x: &mut Block) -> Option<&mut Vec<Block>> {
    match x {
        Block::BlockQuote(c) => Some(c),
        Block::Div(d) => Some(&mut d.content),
        Block::Figure(f) => Some(&mut f.content),
        _ => None,
    }
}

fn str_(s: &str) -> Inline {
    Inline::Str(s.into())
}

impl<O> Interp<'_, O> {
    /// Note a call; whether the rule applies to it.
    fn called(&mut self, rule: &Rule, entry: String) -> bool {
        self.log.push(entry);
        match (rule.modulus, rule.eq) {
            (Some(m), Some(e)) => self.log.len() % m == e,
            _ => true,
        }
    }

    fn rule(&self, tag: &str, kind: &str) -> Option<Rule> {
        self.rules.get(tag).or_else(|| self.rules.get(kind)).cloned()
    }

    fn on_inline(&mut self, x: &mut Inline, skip: &mut dyn FnMut()) -> Option<Vec<Inline>> {
        let tag = inline_tag(x);
        let rule = self.rule(tag, "Inline")?;
        let me = self.log.len() + 1;
        let entry = match inline_text(x) {
            Some(t) => format!("{tag}:{t}"),
            None => tag.to_string(),
        };
        if !self.called(&rule, entry) {
            return None;
        }
        let mark = |x: &mut Inline| {
            if let Some(t) = inline_text(x) {
                t.push_str(&format!("#{me}"));
            }
        };
        match rule.act.as_str() {
            "keep" => None,
            "delete" => Some(vec![]),
            "dup" => Some(vec![x.clone(), x.clone()]),
            "replace" => Some(vec![str_("R")]),
            "wrap" => Some(vec![Inline::Emph(vec![take_inline(x)])]),
            "wrapskip" => {
                skip();
                Some(vec![Inline::Emph(vec![take_inline(x)])])
            }
            "unwrap" => inline_content(x).map(take),
            "mark" => {
                mark(x);
                None
            }
            "markret" => {
                mark(x);
                Some(vec![take_inline(x)])
            }
            "skip" => {
                skip();
                None
            }
            "markskip" => {
                skip();
                mark(x);
                None
            }
            other => panic!("unknown action {other}"),
        }
    }

    fn on_block(&mut self, x: &mut Block, skip: &mut dyn FnMut()) -> Option<Vec<Block>> {
        let tag = block_tag(x);
        let rule = self.rule(tag, "Block")?;
        let me = self.log.len() + 1;
        let entry = match block_text(x) {
            Some(t) => format!("{tag}:{t}"),
            None => tag.to_string(),
        };
        if !self.called(&rule, entry) {
            return None;
        }
        let mark = |x: &mut Block| {
            if let Some(t) = block_text(x) {
                t.push_str(&format!("#{me}"));
            }
        };
        match rule.act.as_str() {
            "keep" => None,
            "delete" => Some(vec![]),
            "dup" => Some(vec![x.clone(), x.clone()]),
            "replace" => Some(vec![Block::Para(vec![str_("R")])]),
            "wrap" => Some(vec![Block::BlockQuote(vec![take_block(x)])]),
            "wrapskip" => {
                skip();
                Some(vec![Block::BlockQuote(vec![take_block(x)])])
            }
            "unwrap" => block_content(x).map(take),
            "mark" => {
                mark(x);
                None
            }
            "markret" => {
                mark(x);
                Some(vec![take_block(x)])
            }
            "skip" => {
                skip();
                None
            }
            "markskip" => {
                skip();
                mark(x);
                None
            }
            other => panic!("unknown action {other}"),
        }
    }

    fn on_list<T>(&mut self, name: &str, xs: &mut Vec<T>, new: fn() -> T, skip: &mut dyn FnMut()) {
        let Some(rule) = self.rules.get(name).cloned() else {
            return;
        };
        if !self.called(&rule, format!("{name}{}", xs.len())) {
            return;
        }
        match rule.act.as_str() {
            "keep" => {}
            "reverse" => xs.reverse(),
            "dropfirst" => {
                if !xs.is_empty() {
                    xs.remove(0);
                }
            }
            "append" => xs.push(new()),
            "empty" => xs.clear(),
            "skip" => skip(),
            other => panic!("unknown action {other}"),
        }
    }

    fn on_meta(&mut self, meta: &mut Meta) {
        let Some(rule) = self.rules.get("Meta").cloned() else {
            return;
        };
        if !self.called(&rule, "Meta".into()) {
            return;
        }
        match rule.act.as_str() {
            "keep" => {}
            "add" => {
                meta.insert("fuzz".into(), MetaValue::MetaBool(true));
            }
            "empty" => meta.clear(),
            other => panic!("unknown action {other}"),
        }
    }

    fn on_pandoc(&mut self, doc: &mut Pandoc) {
        let Some(rule) = self.rules.get("Pandoc").cloned() else {
            return;
        };
        if !self.called(&rule, "Pandoc".into()) {
            return;
        }
        match rule.act.as_str() {
            "keep" => {}
            "prepend" => doc.blocks.insert(0, Block::Para(vec![str_("P")])),
            "clear" => doc.blocks.clear(),
            other => panic!("unknown action {other}"),
        }
    }
}

fn take_inline(x: &mut Inline) -> Inline {
    std::mem::replace(x, Inline::Space)
}

fn take_block(x: &mut Block) -> Block {
    std::mem::replace(x, Block::HorizontalRule)
}

fn new_inline() -> Inline {
    str_("A")
}

fn new_block() -> Block {
    Block::Para(vec![str_("A")])
}

fn no_skip() {
    panic!("skip in an order that isn't top-down");
}

impl Filter for Interp<'_, Typewise> {
    type Order = Typewise;
    fn inline(&mut self, x: &mut Inline, _: &mut Ctx<Typewise>) -> Option<Vec<Inline>> {
        self.on_inline(x, &mut no_skip)
    }
    fn block(&mut self, x: &mut Block, _: &mut Ctx<Typewise>) -> Option<Vec<Block>> {
        self.on_block(x, &mut no_skip)
    }
    fn inlines(&mut self, xs: &mut Vec<Inline>, _: &mut Ctx<Typewise>) {
        self.on_list("Inlines", xs, new_inline, &mut no_skip)
    }
    fn blocks(&mut self, xs: &mut Vec<Block>, _: &mut Ctx<Typewise>) {
        self.on_list("Blocks", xs, new_block, &mut no_skip)
    }
    fn meta(&mut self, meta: &mut Meta, _: &mut Ctx<Typewise>) {
        self.on_meta(meta)
    }
    fn pandoc(&mut self, doc: &mut Pandoc, _: &mut Ctx<Typewise>) {
        self.on_pandoc(doc)
    }
}

impl Filter for Interp<'_, Topdown> {
    type Order = Topdown;
    fn inline(&mut self, x: &mut Inline, ctx: &mut Ctx<Topdown>) -> Option<Vec<Inline>> {
        self.on_inline(x, &mut || ctx.skip_children())
    }
    fn block(&mut self, x: &mut Block, ctx: &mut Ctx<Topdown>) -> Option<Vec<Block>> {
        self.on_block(x, &mut || ctx.skip_children())
    }
    fn inlines(&mut self, xs: &mut Vec<Inline>, ctx: &mut Ctx<Topdown>) {
        self.on_list("Inlines", xs, new_inline, &mut || ctx.skip_children())
    }
    fn blocks(&mut self, xs: &mut Vec<Block>, ctx: &mut Ctx<Topdown>) {
        self.on_list("Blocks", xs, new_block, &mut || ctx.skip_children())
    }
    fn meta(&mut self, meta: &mut Meta, _: &mut Ctx<Topdown>) {
        self.on_meta(meta)
    }
    fn pandoc(&mut self, doc: &mut Pandoc, _: &mut Ctx<Topdown>) {
        self.on_pandoc(doc)
    }
}

fn interp<'a, O: Order>(f: &'a FilterSpec, log: &'a mut Vec<String>) -> Interp<'a, O> {
    Interp {
        rules: &f.rules,
        log,
        order: PhantomData,
    }
}

fn run(spec_line: &str, doc_line: &str) -> Result<String, String> {
    let spec: Option<Spec> = serde_json::from_str(spec_line).map_err(|e| e.to_string())?;
    let mut doc = from_str(doc_line).map_err(|e| e.to_string())?;
    if let Some(spec) = spec {
        let mut log = Vec::new();
        for f in &spec.filters {
            match f.traverse.as_str() {
                "typewise" => apply(&mut doc, &mut interp::<Typewise>(f, &mut log), None),
                "topdown" => apply(&mut doc, &mut interp::<Topdown>(f, &mut log), None),
                other => return Err(format!("unknown order {other}")),
            }
        }
        if spec.log {
            doc.blocks.push(Block::Para(vec![str_(&log.join(" "))]));
        }
    }
    Ok(to_string(&doc))
}

fn main() {
    let mut input = String::new();
    std::io::stdin().read_to_string(&mut input).expect("stdin");
    let mut lines = input.split('\n');
    let stdout = std::io::stdout();
    std::panic::set_hook(Box::new(|_| {}));
    while let (Some(spec), Some(doc)) = (lines.next(), lines.next()) {
        let out = match std::panic::catch_unwind(|| run(spec, doc)) {
            Ok(Ok(out)) => out,
            Ok(Err(e)) => format!("!{}", serde_json::to_string(&e).unwrap()),
            Err(p) => {
                let msg = p
                    .downcast_ref::<String>()
                    .cloned()
                    .or_else(|| p.downcast_ref::<&str>().map(|s| s.to_string()))
                    .unwrap_or_default();
                format!("!{}", serde_json::to_string(&format!("panic: {msg}")).unwrap())
            }
        };
        let mut o = stdout.lock();
        o.write_all(out.as_bytes()).unwrap();
        o.write_all(b"\n").unwrap();
        o.flush().unwrap();
    }
}
