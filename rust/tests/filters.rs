//! The shared filter corpus: each corpus/filters/NAME.lua, written here as
//! Rust filters, must make of its input what pandoc's Lua filter made.

use panir::{
    apply, from_str, to_string, Attr, Block, Ctx, Div, Filter, Inline, Meta, MetaValue, Order,
    Pandoc,
};
use serde_json::value::RawValue;
use serde_json::Value;
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
                Some(vec![Div {
                    attr: Attr::default(),
                    content: take(q),
                }
                .into()])
            }
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

/// The scenario `name` on `doc`; `bottomup` for a stateless one run bottom-up.
/// `None` if there is no Rust version of it.
fn scenario(name: &str, doc: &mut Pandoc, bottomup: bool) -> Option<()> {
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
        "typewise" | "topdown" | "bottomup" => {
            let order = match name {
                "typewise" => Order::Typewise,
                "topdown" => Order::Topdown,
                _ => Order::Bottomup,
            };
            let mut f = Noting::new(order, name == "bottomup");
            apply(doc, &mut f, FORMAT);
            doc.blocks.push(f.listing());
            return Some(());
        }
        _ => return None,
    };
    apply(doc, f.as_mut(), FORMAT);
    Some(())
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
            scenario(name, &mut doc, bottomup)
                .unwrap_or_else(|| panic!("no Rust filter for {name}"));
            let out: Value = serde_json::from_str(&to_string(&doc)).unwrap();
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
