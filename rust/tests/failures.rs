//! A filter function that fails (`Ctx::fail`): the walk stops, and the
//! error says which function, on what, and where (the path in the other
//! languages' form).

use panir::{Block, Ctx, Filter, FilterError, Inline, Meta, Order, Pandoc, apply, from_str};

/// pandoc's JSON of: `title: a *m*`, `x-y: {z: [k]}`; a table with a cell
/// `c`; a list item `[x]{a=1}`.
const DOC: &str = r#"{"pandoc-api-version":[1,23,1,2],"meta":{"title":{"t":"MetaInlines","c":[{"t":"Str","c":"a"},{"t":"Space"},{"t":"Emph","c":[{"t":"Str","c":"m"}]}]},"x-y":{"t":"MetaMap","c":{"z":{"t":"MetaList","c":[{"t":"MetaInlines","c":[{"t":"Str","c":"k"}]}]}}}},"blocks":[{"t":"Table","c":[["",[],[]],[null,[]],[[{"t":"AlignDefault"},{"t":"ColWidthDefault"}]],[["",[],[]],[[["",[],[]],[[["",[],[]],{"t":"AlignDefault"},1,1,[{"t":"Plain","c":[{"t":"Str","c":"a"}]}]]]]]],[[["",[],[]],0,[],[[["",[],[]],[[["",[],[]],{"t":"AlignDefault"},1,1,[{"t":"Plain","c":[{"t":"Str","c":"c"}]}]]]]]]],[["",[],[]],[]]]},{"t":"BulletList","c":[[{"t":"Plain","c":[{"t":"Span","c":[["",[],[["a","1"]]],[{"t":"Str","c":"x"}]]}]}]]}]}"#;

/// Fails on the Str whose text is `on`; upper-cases the others, and
/// deletes each `Space` (so that the list is spliced before the failure).
struct FailOn(&'static str, Order);

impl Filter for FailOn {
    fn order(&self) -> Order {
        self.1
    }
    fn inline(&mut self, x: &mut Inline, ctx: &mut Ctx) -> Option<Vec<Inline>> {
        match x {
            Inline::Str(s) if s == self.0 => ctx.fail(format!("no {s}, please")),
            Inline::Str(s) => Some(vec![Inline::Str(s.to_uppercase().into())]),
            Inline::Space => Some(vec![]),
            _ => None,
        }
    }
}

fn doc() -> Pandoc {
    from_str(DOC).unwrap()
}

#[test]
fn the_error_says_where_in_every_order() {
    for order in [Order::Typewise, Order::Topdown, Order::Bottomup] {
        for (text, path) in [
            // (the Space before it was deleted: the path is in the
            // document as it is now)
            ("m", "meta.title.content[1].content[0]"),
            ("k", "meta['x-y'].content.z.content[0].content[0]"),
            (
                "c",
                "blocks[0].bodies[0].body[0].cells[0].content[0].content[0]",
            ),
            ("x", "blocks[1].content[0][0].content[0].content[0]"),
        ] {
            let e = apply(&mut doc(), &mut FailOn(text, order), None).unwrap_err();
            assert_eq!(
                (e.path.as_str(), e.function, e.node),
                (path, Some("inline"), "Str"),
                "{order:?}"
            );
            assert_eq!(e.error.to_string(), format!("no {text}, please"));
            assert_eq!(
                e.to_string(),
                format!("no {text}, please\n  in filter function inline, on the Str at {path}")
            );
        }
    }
}

#[test]
fn the_walk_stops_and_nothing_is_lost() {
    // the title's Space was deleted and its "a" upper-cased; then "m" failed,
    // and nothing after it was walked
    let mut d = doc();
    let e = apply(&mut d, &mut FailOn("m", Order::Topdown), None).unwrap_err();
    assert_eq!(e.path, "meta.title.content[1].content[0]");
    let title = panir::stringify(&d.meta["title"]);
    assert_eq!(title, "Am");
    assert_eq!(panir::stringify(&d.blocks), "acx");
    // a list spliced before the failure, then left whole
    let mut xs = panir::inlines("p q r s");
    let e = apply(&mut xs, &mut FailOn("r", Order::Bottomup), None).unwrap_err();
    assert_eq!(e.path, "inlines[2]");
    assert_eq!(
        xs,
        vec![
            "P".into(),
            "Q".into(),
            "r".into(),
            Inline::Space,
            "s".into()
        ]
    );
}

/// Fails in the function named `on`.
struct FailIn(&'static str, Order);

impl Filter for FailIn {
    fn order(&self) -> Order {
        self.1
    }
    fn block(&mut self, x: &mut Block, ctx: &mut Ctx) -> Option<Vec<Block>> {
        match x {
            Block::Plain(_) if self.0 == "block" => ctx.fail("block"),
            _ => None,
        }
    }
    fn inlines(&mut self, xs: &mut Vec<Inline>, ctx: &mut Ctx) -> Option<()> {
        if self.0 == "inlines" && xs.first() == Some(&"k".into()) {
            return ctx.fail("inlines");
        }
        Some(())
    }
    fn blocks(&mut self, xs: &mut Vec<Block>, ctx: &mut Ctx) -> Option<()> {
        if self.0 == "blocks" && matches!(xs.first(), Some(Block::Table(_))) {
            return ctx.fail("blocks");
        }
        Some(())
    }
    fn meta(&mut self, _: &mut Meta, ctx: &mut Ctx) -> Option<()> {
        if self.0 == "meta" {
            return ctx.fail("meta");
        }
        Some(())
    }
    fn pandoc(&mut self, _: &mut Pandoc, ctx: &mut Ctx) -> Option<()> {
        if self.0 == "pandoc" {
            return ctx.fail("pandoc");
        }
        Some(())
    }
}

#[test]
fn every_function_can_fail() {
    for order in [Order::Typewise, Order::Topdown, Order::Bottomup] {
        let failed = |name| apply(&mut doc(), &mut FailIn(name, order), None).unwrap_err();
        let e: FilterError = failed("block");
        assert_eq!(
            (e.function, e.node, e.path.as_str()),
            (
                Some("block"),
                "Plain",
                "blocks[0].head.rows[0].cells[0].content[0]"
            )
        );
        let e = failed("inlines");
        assert_eq!(
            (e.function, e.node, e.path.as_str()),
            (
                Some("inlines"),
                "Inlines",
                "meta['x-y'].content.z.content[0].content"
            )
        );
        let e = failed("blocks");
        assert_eq!(
            (e.function, e.node, e.path.as_str()),
            (Some("blocks"), "Blocks", "blocks")
        );
        let e = failed("meta");
        assert_eq!(
            (e.function, e.node, e.path.as_str()),
            (Some("meta"), "Meta", "meta")
        );
        assert_eq!(
            e.to_string(),
            "meta\n  in filter function meta, on the Meta at meta"
        );
        let e = failed("pandoc");
        assert_eq!(
            (e.function, e.node, e.path.as_str()),
            (Some("pandoc"), "Pandoc", "")
        );
        assert_eq!(
            e.to_string(),
            "pandoc\n  in filter function pandoc, on the Pandoc"
        );
    }
    // a fragment's paths are from it, after its name
    let mut div = Block::Div(Box::new(panir::Div {
        attr: Default::default(),
        content: vec![Block::Para(vec![]), Block::Plain(vec![])],
    }));
    let e = apply(&mut div, &mut FailIn("block", Order::Typewise), None).unwrap_err();
    assert_eq!(e.path, "Div.content[1]");
    // an empty list has no place of its own: no path
    struct Empty;
    impl Filter for Empty {
        fn inlines(&mut self, xs: &mut Vec<Inline>, ctx: &mut Ctx) -> Option<()> {
            if xs.is_empty() {
                return ctx.fail("empty");
            }
            Some(())
        }
    }
    let e = apply(&mut vec![Block::Para(vec![])], &mut Empty, None).unwrap_err();
    assert_eq!(e.path, "");
    assert_eq!(
        e.to_string(),
        "empty\n  in filter function inlines, on the Inlines"
    );
}

/// Upper-cases each Str, in the conversion's format; fails on `x`.
struct Caps;

impl Filter for Caps {
    fn inline(&mut self, x: &mut Inline, ctx: &mut Ctx) -> Option<Vec<Inline>> {
        let Inline::Str(s) = x else { return None };
        if s == "x" {
            return ctx.fail("no x, please");
        }
        *s = format!("{}:{}", s.to_uppercase(), ctx.format().unwrap_or("-")).into();
        None
    }
}

/// An emphasis becomes its content, walked with `Caps` (pandocfilters'
/// deemph), with `Ctx::apply`.
struct Deemph;

impl Filter for Deemph {
    fn order(&self) -> Order {
        Order::Topdown
    }
    fn inline(&mut self, x: &mut Inline, ctx: &mut Ctx) -> Option<Vec<Inline>> {
        let Inline::Emph(content) = x else {
            return None;
        };
        ctx.apply(content, &mut Caps)?;
        Some(std::mem::take(content))
    }
}

#[test]
fn a_walk_from_a_function_has_its_conversion_and_its_error() {
    let para = |xs| Pandoc::new(vec![Block::Para(xs)]);
    let mut doc = para(vec!["a".into(), Inline::Emph(panir::inlines("b c"))]);
    apply(&mut doc, &mut Deemph, Some("html")).unwrap();
    assert_eq!(panir::stringify(&doc), "aB:html C:html");
    // the inner function's error, where it was in the fragment, as Python
    // and TypeScript report one (noted once, where it was raised)
    let mut doc = para(vec![
        "a".into(),
        Inline::Emph(vec!["b".into(), Inline::Space, "x".into()]),
    ]);
    let e = apply(&mut doc, &mut Deemph, None).unwrap_err();
    assert_eq!(
        (e.function, e.node, e.path.as_str()),
        (Some("inline"), "Str", "inlines[2]")
    );
    assert_eq!(
        e.to_string(),
        "no x, please\n  in filter function inline, on the Str at inlines[2]"
    );
    // the walk stopped there: the emphasis is as the inner walk left it
    assert_eq!(panir::stringify(&doc), "aB:- x");
    // a FilterError given to ctx.fail is passed up the same way
    struct ByHand;
    impl Filter for ByHand {
        fn inline(&mut self, x: &mut Inline, ctx: &mut Ctx) -> Option<Vec<Inline>> {
            let Inline::Emph(content) = x else {
                return None;
            };
            match panir::apply_with(content, &mut Caps, ctx.conversion()) {
                Ok(()) => None,
                Err(e) => ctx.fail(e),
            }
        }
    }
    let mut doc = para(vec![Inline::Emph(vec!["x".into()])]);
    let e = apply(&mut doc, &mut ByHand, None).unwrap_err();
    assert_eq!(e.path, "inlines[0]");
}

#[test]
fn a_walk_from_a_meta_or_pandoc_function_says_its_own_path() {
    struct InMeta;
    impl Filter for InMeta {
        fn meta(&mut self, meta: &mut Meta, ctx: &mut Ctx) -> Option<()> {
            if let Some(panir::MetaValue::MetaInlines(xs)) = meta.get_mut("title") {
                ctx.apply(xs, &mut Caps)?;
            }
            Some(())
        }
    }
    let mut doc = Pandoc::new(vec![]);
    doc.meta.insert(
        "title".into(),
        panir::MetaValue::MetaInlines(panir::inlines("a x")),
    );
    let e = apply(&mut doc, &mut InMeta, None).unwrap_err();
    assert_eq!(
        (e.function, e.path.as_str()),
        (Some("inline"), "inlines[2]")
    );
    // a Meta function's own error is at `meta`, as before
    struct Fails;
    impl Filter for Fails {
        fn meta(&mut self, _: &mut Meta, ctx: &mut Ctx) -> Option<()> {
            ctx.fail("no")
        }
    }
    let e = apply(&mut doc, &mut Fails, None).unwrap_err();
    assert_eq!((e.function, e.path.as_str()), (Some("meta"), "meta"));
}

#[test]
fn every_function_takes_question_marks_the_same_way() {
    // `?` on an `Option` in `meta` or `pandoc` returns early, keeping what
    // was done: `None` is no failure
    struct Early(Vec<&'static str>);
    impl Filter for Early {
        fn meta(&mut self, meta: &mut Meta, _: &mut Ctx) -> Option<()> {
            self.0.push("meta");
            meta.get("missing")?;
            self.0.push("never");
            Some(())
        }
        fn pandoc(&mut self, doc: &mut Pandoc, ctx: &mut Ctx) -> Option<()> {
            self.0.push("pandoc");
            ctx.apply(&mut doc.blocks, &mut Caps)?;
            let first = doc.blocks.first()?;
            if first.inlines()?.is_empty() {
                return ctx.fail("an empty paragraph");
            }
            Some(())
        }
    }
    let mut doc = Pandoc::new(vec![Block::Para(panir::inlines("a b"))]);
    let mut f = Early(vec![]);
    apply(&mut doc, &mut f, None).unwrap();
    assert_eq!(f.0, ["meta", "pandoc"]);
    assert_eq!(panir::stringify(&doc), "A:- B:-");
    let mut doc = Pandoc::new(vec![Block::Para(vec![])]);
    let e = apply(&mut doc, &mut Early(vec![]), None).unwrap_err();
    assert_eq!(
        e.to_string(),
        "an empty paragraph\n  in filter function pandoc, on the Pandoc"
    );
}
