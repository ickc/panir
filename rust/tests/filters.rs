//! The shared filter corpus: each corpus/filters/NAME.lua, written here as
//! Rust filters, must make of its input what pandoc's Lua filter made.

use panir::{
    Attr, Block, Ctx, Div, Filter, FilterError, Header, Inline, Meta, MetaValue, Order, Pandoc,
    apply, from_str, to_string,
};
use serde_json::Value;
use serde_json::value::RawValue;
use std::mem::take;
use std::path::PathBuf;

const FORMAT: Option<&str> = Some("json");

fn corpus_dir() -> PathBuf {
    [env!("CARGO_MANIFEST_DIR"), "..", "corpus"]
        .iter()
        .collect()
}

fn str_(s: &str) -> Inline {
    Inline::Str(s.into())
}

// Filters whose functions don't depend on each other's calls, in any order.

struct Upper(Order);

impl Filter for Upper {
    fn order(&self) -> Order {
        self.0
    }
    fn inline(&mut self, x: &mut Inline, _: &mut Ctx) -> Option<Vec<Inline>> {
        match x {
            Inline::Str(s) => Some(vec![Inline::Str(s.to_uppercase().into())]),
            _ => None,
        }
    }
}

struct Modify(Order);

impl Filter for Modify {
    fn order(&self) -> Order {
        self.0
    }
    fn block(&mut self, x: &mut Block, _: &mut Ctx) -> Option<Vec<Block>> {
        match x {
            Block::Header(h) => h.level += 1,
            Block::CodeBlock(c) => c.attr.classes.push("numbered".into()),
            _ => {}
        }
        None
    }
    fn inline(&mut self, x: &mut Inline, _: &mut Ctx) -> Option<Vec<Inline>> {
        match x {
            Inline::Link(l) => {
                l.target.url = format!("https://example.org/{}", l.target.url).into()
            }
            Inline::Image(i) => {
                i.target.url = format!("img/{}", i.target.url).into();
                i.attr.attributes.push(("loading".into(), "lazy".into()));
            }
            _ => {}
        }
        None
    }
}

struct Splice(Order);

impl Filter for Splice {
    fn order(&self) -> Order {
        self.0
    }
    fn inline(&mut self, x: &mut Inline, _: &mut Ctx) -> Option<Vec<Inline>> {
        match x {
            Inline::Note(_) | Inline::Emph(_) => Some(vec![]),
            Inline::Strong(c) => Some(take(c)),
            _ => None,
        }
    }
    fn block(&mut self, x: &mut Block, _: &mut Ctx) -> Option<Vec<Block>> {
        match x {
            Block::Div(d) => Some(take(&mut d.content)),
            Block::HorizontalRule => Some(vec![
                Block::Para(vec![str_("one")]),
                Block::Para(vec![str_("two")]),
            ]),
            _ => None,
        }
    }
}

// Rust's one function per kind matches on the constructor, so the Lua
// scenario's point (a constructor's function wins over its type's) is
// written as the order of the match.
struct Generic(Order);

impl Filter for Generic {
    fn order(&self) -> Order {
        self.0
    }
    fn inline(&mut self, x: &mut Inline, _: &mut Ctx) -> Option<Vec<Inline>> {
        match x {
            Inline::Str(s) => Some(vec![Inline::Str(format!("{s}!").into())]),
            Inline::Code(c) => Some(vec![Inline::Str(c.text.clone())]),
            _ => None,
        }
    }
    fn block(&mut self, x: &mut Block, _: &mut Ctx) -> Option<Vec<Block>> {
        match x {
            Block::CodeBlock(c) => Some(vec![Block::Para(vec![Inline::Str(c.text.clone())])]),
            _ => None,
        }
    }
}

struct Once(Order);

impl Filter for Once {
    fn order(&self) -> Order {
        self.0
    }
    fn inline(&mut self, x: &mut Inline, _: &mut Ctx) -> Option<Vec<Inline>> {
        match x {
            Inline::Str(s) if s == "Some" => Some(vec![Inline::Emph(vec![str_("new")])]),
            Inline::Emph(c) => Some(vec![Inline::Strong(take(c))]),
            _ => None,
        }
    }
}

struct Lists(Order);

impl Filter for Lists {
    fn order(&self) -> Order {
        self.0
    }
    fn inlines(&mut self, xs: &mut Vec<Inline>, _: &mut Ctx) {
        let n = xs.len();
        xs.retain(|x| !matches!(x, Inline::Space));
        xs.push(Inline::Str(format!("<{n}>").into()));
    }
    fn blocks(&mut self, xs: &mut Vec<Block>, _: &mut Ctx) {
        *xs = take(xs)
            .into_iter()
            .flat_map(|b| {
                let rule = matches!(b, Block::Header(_)).then_some(Block::HorizontalRule);
                std::iter::once(b).chain(rule)
            })
            .collect();
    }
}

struct MetaScenario(Order);

impl Filter for MetaScenario {
    fn order(&self) -> Order {
        self.0
    }
    fn meta(&mut self, m: &mut Meta, ctx: &mut Ctx) {
        m.insert("draft".into(), MetaValue::MetaBool(true));
        m.insert(
            "format".into(),
            MetaValue::MetaString(ctx.format().unwrap_or("").into()),
        );
        m.insert(
            "tags".into(),
            MetaValue::MetaList(vec![
                MetaValue::MetaString("a".into()),
                MetaValue::MetaInlines(vec![str_("b")]),
            ]),
        );
        m.remove("count");
    }
    fn pandoc(&mut self, doc: &mut Pandoc, _: &mut Ctx) {
        let draft = matches!(doc.meta.get("draft"), Some(MetaValue::MetaBool(true)));
        doc.blocks.insert(
            0,
            Block::Para(vec![str_(if draft { "draft" } else { "final" })]),
        );
    }
}

struct Twice(Order);

impl Filter for Twice {
    fn order(&self) -> Order {
        self.0
    }
    fn inline(&mut self, x: &mut Inline, _: &mut Ctx) -> Option<Vec<Inline>> {
        match x {
            Inline::Code(_) => Some(vec![x.clone(), x.clone()]),
            _ => None,
        }
    }
    fn block(&mut self, x: &mut Block, _: &mut Ctx) -> Option<Vec<Block>> {
        if let Block::Para(content) = x
            && let Some(Inline::Code(c)) = content.iter_mut().find(|i| matches!(i, Inline::Code(_)))
        {
            c.text = "changed".into();
        }
        None
    }
}

// A metadata function adding a key, then a filter counting its calls.

struct AddKey;

impl Filter for AddKey {
    fn meta(&mut self, m: &mut Meta, _: &mut Ctx) {
        m.insert("added".into(), MetaValue::MetaInlines(vec![str_("new")]));
    }
}

struct Count(usize);

impl Filter for Count {
    fn inline(&mut self, x: &mut Inline, _: &mut Ctx) -> Option<Vec<Inline>> {
        match x {
            Inline::Str(s) => {
                self.0 += 1;
                Some(vec![Inline::Str(format!("{s}{}", self.0).into())])
            }
            _ => None,
        }
    }
    fn meta(&mut self, m: &mut Meta, _: &mut Ctx) {
        self.0 += 1;
        m.insert(
            "calls".into(),
            MetaValue::MetaString(self.0.to_string().into()),
        );
    }
    fn pandoc(&mut self, doc: &mut Pandoc, _: &mut Ctx) {
        self.0 += 1;
        let calls = panir::inlines(&format!("calls: {}", self.0));
        doc.blocks.push(Block::Para(calls));
    }
}

// Noting each call, as the Lua scenarios do.

struct Noting {
    seen: Vec<String>,
    strong: bool,
    order: Order,
}

impl Noting {
    fn new(order: Order, strong: bool) -> Self {
        Noting {
            seen: vec![],
            strong,
            order,
        }
    }

    /// The paragraph listing the notes.
    fn listing(&self) -> Block {
        Block::Para(panir::inlines(&self.seen.join(" ")))
    }
}

impl Filter for Noting {
    fn order(&self) -> Order {
        self.order
    }
    fn inline(&mut self, x: &mut Inline, _: &mut Ctx) -> Option<Vec<Inline>> {
        let note = match x {
            Inline::Str(s) => format!("Str:{s}"),
            Inline::Code(c) => format!("Code:{}", c.text),
            Inline::Emph(_) => "Emph".into(),
            Inline::Strong(_) if self.strong => "Strong".into(),
            Inline::Note(_) => "Note".into(),
            Inline::Link(_) => "Link".into(),
            _ => return None,
        };
        self.seen.push(note);
        None
    }
    fn block(&mut self, x: &mut Block, _: &mut Ctx) -> Option<Vec<Block>> {
        let note = match x {
            Block::Para(_) => "Para",
            Block::Header(_) => "Header",
            Block::Div(_) => "Div",
            Block::Table(_) => "Table",
            Block::Figure(_) => "Figure",
            Block::BlockQuote(_) => "BlockQuote",
            _ => return None,
        };
        self.seen.push(note.into());
        None
    }
    fn inlines(&mut self, xs: &mut Vec<Inline>, _: &mut Ctx) {
        self.seen.push(format!("Inlines{}", xs.len()));
    }
    fn blocks(&mut self, xs: &mut Vec<Block>, _: &mut Ctx) {
        self.seen.push(format!("Blocks{}", xs.len()));
    }
    fn meta(&mut self, _: &mut Meta, _: &mut Ctx) {
        self.seen.push("Meta".into());
    }
    fn pandoc(&mut self, _: &mut Pandoc, _: &mut Ctx) {
        self.seen.push("Pandoc".into());
    }
}

// Top-down.

struct Skip;

impl Filter for Skip {
    fn order(&self) -> Order {
        Order::Topdown
    }
    fn inline(&mut self, x: &mut Inline, ctx: &mut Ctx) -> Option<Vec<Inline>> {
        match x {
            Inline::Str(s) => Some(vec![Inline::Str(s.to_uppercase().into())]),
            Inline::Emph(_) => {
                ctx.skip_children();
                None
            }
            _ => None,
        }
    }
    fn block(&mut self, x: &mut Block, ctx: &mut Ctx) -> Option<Vec<Block>> {
        match x {
            Block::Header(h) => Some(vec![Block::Para(take(&mut h.content))]),
            Block::Div(_) => {
                ctx.skip_children();
                None
            }
            Block::BlockQuote(q) => {
                ctx.skip_children();
                Some(vec![
                    Div {
                        attr: Attr::default(),
                        content: take(q),
                    }
                    .into(),
                ])
            }
            _ => None,
        }
    }
}

/// A new document, which is what is walked.
struct NewDoc;

impl Filter for NewDoc {
    fn order(&self) -> Order {
        Order::Topdown
    }
    fn pandoc(&mut self, doc: &mut Pandoc, _: &mut Ctx) {
        let Some(MetaValue::MetaInlines(title)) = doc.meta.get("title") else {
            panic!("no title");
        };
        let header = Header {
            level: 1,
            attr: Attr::default(),
            content: title.clone(),
        };
        let mut new = Pandoc::new(vec![
            header.into(),
            Block::Para(panir::inlines("made anew")),
        ]);
        new.meta = take(&mut doc.meta);
        *doc = new;
    }
    fn inline(&mut self, x: &mut Inline, _: &mut Ctx) -> Option<Vec<Inline>> {
        match x {
            Inline::Str(s) => Some(vec![Inline::Str(s.to_uppercase().into())]),
            _ => None,
        }
    }
}

struct TopLists;

impl Filter for TopLists {
    fn order(&self) -> Order {
        Order::Topdown
    }
    fn inlines(&mut self, xs: &mut Vec<Inline>, ctx: &mut Ctx) {
        if xs.len() == 1 {
            ctx.skip_children();
        } else {
            xs.reverse();
        }
    }
    fn inline(&mut self, x: &mut Inline, _: &mut Ctx) -> Option<Vec<Inline>> {
        match x {
            Inline::Str(s) => Some(vec![Inline::Str(s.to_uppercase().into())]),
            _ => None,
        }
    }
}

/// Attributes by key, a key perhaps in two pairs.
struct Attributes(Order);

impl Filter for Attributes {
    fn order(&self) -> Order {
        self.0
    }
    fn inline(&mut self, x: &mut Inline, _: &mut Ctx) -> Option<Vec<Inline>> {
        let Inline::Span(s) = x else { return None };
        if let Some(k) = s.attr.get("k").map(str::to_owned) {
            s.attr.set("first", k.as_str());
            s.attr.set("k", k + "!");
        }
        s.attr.remove("drop");
        if s.attr.has_class("mark") {
            s.attr.classes.push("seen".into());
        }
        None
    }
}

/// Fails on the second paragraph, after changing the first.
struct Fails(usize);

impl Filter for Fails {
    fn block(&mut self, x: &mut Block, ctx: &mut Ctx) -> Option<Vec<Block>> {
        let Block::Para(content) = x else { return None };
        self.0 += 1;
        if self.0 == 2 {
            return ctx.fail("the second paragraph");
        }
        content.push(str_("!"));
        None
    }
}

/// Top-down: what a function returns is not given to a function again.
#[derive(Default)]
struct Replaced {
    seen: Vec<String>,
}

impl Filter for Replaced {
    fn order(&self) -> Order {
        Order::Topdown
    }
    fn inline(&mut self, x: &mut Inline, _: &mut Ctx) -> Option<Vec<Inline>> {
        match x {
            Inline::Emph(content) => {
                self.seen.push("Emph".into());
                Some(take(content))
            }
            Inline::Strong(_) => {
                let text = panir::stringify(&*x);
                self.seen.push(format!("Strong:{text}"));
                match text.as_str() {
                    "other" => Some(vec![Inline::Underline(vec![
                        Inline::Strong(vec![str_("in")]),
                        Inline::Emph(vec![str_("e")]),
                    ])]),
                    "two" => Some(vec![
                        Inline::Strong(vec![str_("s")]),
                        Inline::Underline(vec![Inline::Strong(vec![str_("u")])]),
                    ]),
                    _ => None,
                }
            }
            Inline::Underline(_) => {
                self.seen.push("Underline".into());
                None
            }
            Inline::Str(s) => {
                self.seen.push(format!("Str:{s}"));
                None
            }
            _ => None,
        }
    }
    fn block(&mut self, x: &mut Block, _: &mut Ctx) -> Option<Vec<Block>> {
        let Block::Div(d) = x else { return None };
        self.seen.push("Div".into());
        Some(take(&mut d.content))
    }
    fn inlines(&mut self, xs: &mut Vec<Inline>, _: &mut Ctx) {
        if matches!(xs.first(), Some(Inline::Str(s)) if s == "list") {
            self.seen.push("Inlines".into());
            *xs = vec![
                Inline::Emph(vec![str_("new")]),
                Inline::Strong(vec![Inline::Emph(vec![str_("g")])]),
            ];
        }
    }
}

/// A function for metadata values, typewise then top-down.
struct MetaValues<'a>(Order, &'a mut Vec<String>);

impl Filter for MetaValues<'_> {
    fn order(&self) -> Order {
        self.0
    }
    fn meta_value(&mut self, x: &mut MetaValue, _: &mut Ctx) -> Option<Vec<MetaValue>> {
        let note = match x {
            MetaValue::MetaInlines(_) => {
                let text = panir::stringify(&*x);
                self.1.push(format!("MetaInlines:{text}"));
                return match text.as_str() {
                    "drop" => Some(vec![]),
                    "two" => Some(vec![
                        MetaValue::MetaString("two-a".into()),
                        MetaValue::MetaString("two-b".into()),
                    ]),
                    _ => None,
                };
            }
            MetaValue::MetaBool(b) => {
                let text = b.to_string();
                self.1.push("MetaBool".into());
                return Some(vec![MetaValue::MetaString(text.into())]);
            }
            MetaValue::MetaBlocks(_) => "MetaBlocks".into(),
            MetaValue::MetaList(xs) => format!("MetaList:{}", xs.len()),
            MetaValue::MetaMap(_) => "MetaMap".into(),
            MetaValue::MetaString(s) => format!("MetaString:{s}"),
        };
        self.1.push(note);
        None
    }
    fn inline(&mut self, x: &mut Inline, _: &mut Ctx) -> Option<Vec<Inline>> {
        if let Inline::Str(s) = x {
            self.1.push(format!("Str:{s}"));
        }
        None
    }
}

/// The scenario `name` on `doc`; `bottomup` for a stateless one run bottom-up.
/// `None` if there is no Rust version of it.
fn scenario(name: &str, doc: &mut Pandoc, bottomup: bool) -> Option<Result<(), FilterError>> {
    let order = if bottomup {
        Order::Bottomup
    } else {
        Order::Typewise
    };
    // (as trait objects: a filter chosen at run time)
    let mut f: Box<dyn Filter> = match name {
        "upper" => Box::new(Upper(order)),
        "modify" => Box::new(Modify(order)),
        "splice" => Box::new(Splice(order)),
        "generic" => Box::new(Generic(order)),
        "once" => Box::new(Once(order)),
        "lists" => Box::new(Lists(order)),
        "meta" => Box::new(MetaScenario(order)),
        "skip" => Box::new(Skip),
        "toplists" => Box::new(TopLists),
        "newdoc" => Box::new(NewDoc),
        "twice" => Box::new(Twice(order)),
        "attributes" => Box::new(Attributes(order)),
        "fails" => Box::new(Fails(0)),
        "metacount" => {
            return Some(
                apply(doc, &mut AddKey, FORMAT).and_then(|()| apply(doc, &mut Count(0), FORMAT)),
            );
        }
        "metavalues" => {
            let mut seen = vec![];
            let done = apply(doc, &mut MetaValues(Order::Typewise, &mut seen), FORMAT)
                .and_then(|()| apply(doc, &mut MetaValues(Order::Topdown, &mut seen), FORMAT));
            doc.blocks
                .push(Block::Para(panir::inlines(&seen.join(" "))));
            return Some(done);
        }
        "replaced" => {
            let mut f = Replaced::default();
            let done = apply(doc, &mut f, FORMAT);
            doc.blocks
                .push(Block::Para(panir::inlines(&f.seen.join(" "))));
            return Some(done);
        }
        "typewise" | "topdown" | "bottomup" => {
            let order = match name {
                "typewise" => Order::Typewise,
                "topdown" => Order::Topdown,
                _ => Order::Bottomup,
            };
            let mut f = Noting::new(order, name == "bottomup");
            let done = apply(doc, &mut f, FORMAT);
            doc.blocks.push(f.listing());
            return Some(done);
        }
        _ => return None,
    };
    Some(apply(doc, f.as_mut(), FORMAT))
}

#[test]
fn as_pandocs_lua() {
    #[derive(serde::Deserialize)]
    struct Line<'a> {
        name: String,
        also: Vec<String>,
        // as pandoc wrote it: serde_json::Value would sort the keys, and
        // read only contents before their tag
        #[serde(borrow)]
        input: &'a RawValue,
        output: Value,
    }
    let text = std::fs::read_to_string(corpus_dir().join("filters.jsonl")).unwrap();
    let lines: Vec<Line> = text
        .split('\n')
        .filter(|l| !l.is_empty())
        .map(|l| serde_json::from_str(l).unwrap())
        .collect();
    let mut lua: Vec<String> = std::fs::read_dir(corpus_dir().join("filters"))
        .unwrap()
        .filter_map(|e| {
            e.unwrap()
                .file_name()
                .into_string()
                .ok()?
                .strip_suffix(".lua")
                .map(String::from)
        })
        .collect();
    lua.sort();
    let mut names: Vec<String> = lines.iter().map(|l| l.name.clone()).collect();
    names.sort();
    assert_eq!(
        names, lua,
        "corpus/filters.jsonl is stale: run scripts/filters-corpus.sh"
    );
    let mut failed = vec![];
    for line in &lines {
        let name = line.name.as_str();
        assert!(line.input.get().starts_with(r#"{"pandoc-api-version":"#));
        let mut runs = vec![false];
        runs.extend(line.also.iter().map(|t| {
            assert_eq!(t, "bottomup");
            true
        }));
        for bottomup in runs {
            let mut doc = from_str(line.input.get()).unwrap();
            let done = scenario(name, &mut doc, bottomup)
                .unwrap_or_else(|| panic!("no Rust filter for {name}"));
            // (a scenario pandoc fails on: null, and the filter fails too)
            let out = match done {
                Ok(()) => serde_json::from_str(&to_string(&doc)).unwrap(),
                Err(_) => Value::Null,
            };
            if out != line.output {
                failed.push(format!(
                    "{name}{}",
                    if bottomup { ", bottomup" } else { "" }
                ));
            }
        }
    }
    assert!(failed.is_empty(), "differ from pandoc's Lua: {failed:?}");
}
