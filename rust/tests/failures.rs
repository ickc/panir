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
    fn inlines(&mut self, xs: &mut Vec<Inline>, ctx: &mut Ctx) {
        if self.0 == "inlines" && xs.first() == Some(&"k".into()) {
            ctx.fail::<()>("inlines");
        }
    }
    fn blocks(&mut self, xs: &mut Vec<Block>, ctx: &mut Ctx) {
        if self.0 == "blocks" && matches!(xs.first(), Some(Block::Table(_))) {
            ctx.fail::<()>("blocks");
        }
    }
    fn meta(&mut self, _: &mut Meta, ctx: &mut Ctx) {
        if self.0 == "meta" {
            ctx.fail::<()>("meta");
        }
    }
    fn pandoc(&mut self, _: &mut Pandoc, ctx: &mut Ctx) {
        if self.0 == "pandoc" {
            ctx.fail::<()>("pandoc");
        }
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
        fn inlines(&mut self, xs: &mut Vec<Inline>, ctx: &mut Ctx) {
            if xs.is_empty() {
                ctx.fail::<()>("empty");
            }
        }
    }
    let e = apply(&mut vec![Block::Para(vec![])], &mut Empty, None).unwrap_err();
    assert_eq!(e.path, "");
    assert_eq!(
        e.to_string(),
        "empty\n  in filter function inlines, on the Inlines"
    );
}
