//! The edges: what a review and fuzzing against pandoc found (fuzz/).

use panir::{
    Block, ColSpec, ColWidth, Ctx, Filter, Inline, MAX_DEPTH, Order, Pandoc, Table, apply,
    from_str, to_string,
};

/// A function for every node and list, which changes nothing.
struct Every(Order);

impl Filter for Every {
    fn order(&self) -> Order {
        self.0
    }
    fn block(&mut self, _: &mut Block, _: &mut Ctx) -> Option<Vec<Block>> {
        None
    }
    fn inline(&mut self, _: &mut Inline, _: &mut Ctx) -> Option<Vec<Inline>> {
        None
    }
}

const HEAD: &str = r#"{"pandoc-api-version":[1,23,1,2],"meta":{},"blocks":["#;

fn table(width: &str) -> String {
    format!(
        r#"{HEAD}{{"t":"Table","c":[["",[],[]],[null,[]],[[{{"t":"AlignDefault"}},{{"t":"ColWidth","c":{width}}}]],[["",[],[]],[]],[],[["",[],[]],[]]]}}]}}"#
    )
}

fn width(doc: &Pandoc) -> f64 {
    match &doc.blocks[0] {
        Block::Table(t) => match t.col_specs[0].width {
            ColWidth::ColWidth(w) => w,
            ColWidth::ColWidthDefault => panic!("no width"),
        },
        _ => panic!("no table"),
    }
}

/// `depth` of `open`...`close` around a paragraph, in a document.
fn nested(open: &str, close: &str, depth: usize) -> String {
    format!(
        r#"{HEAD}{}{{"t":"Para","c":[]}}{}]}}"#,
        open.repeat(depth),
        close.repeat(depth)
    )
}

const DIV: (&str, &str) = (r#"{"t":"Div","c":[["",[],[]],["#, "]]}");
const TABLE: (&str, &str) = (
    r#"{"t":"Table","c":[["",[],[]],[null,[]],[],[["",[],[]],[]],[[["",[],[]],0,[],[[["",[],[]],[[["",[],[]],{"t":"AlignDefault"},1,1,["#,
    r#"]]]]]]],[["",[],[]],[]]]}"#,
);
// not pandoc's order: the contents before the tag
const DIV_C_FIRST: (&str, &str) = (r#"{"c":[["",[],[]],["#, r#"]],"t":"Div"}"#);

#[test]
fn doubles_that_are_not_finite() {
    // as pandoc writes them (a column `width="0*"` in HTML gives null)
    for (written, value) in [
        ("null", f64::NAN),
        (r#""+inf""#, f64::INFINITY),
        (r#""-inf""#, f64::NEG_INFINITY),
    ] {
        let doc = from_str(&table(written)).unwrap();
        let w = width(&doc);
        assert!(
            w == value || (w.is_nan() && value.is_nan()),
            "{written}: {w}"
        );
        assert_eq!(to_string(&doc), table(written));
        let made = Pandoc::new(vec![
            Table {
                col_specs: vec![ColSpec {
                    width: ColWidth::ColWidth(value),
                    ..Default::default()
                }],
                ..Default::default()
            }
            .into(),
        ]);
        assert_eq!(to_string(&made), table(written));
    }
    assert_eq!(width(&from_str(&table("1")).unwrap()), 1.0);
    for bad in [r#""inf""#, "true", r#""0.5""#] {
        let e = from_str(&table(bad)).unwrap_err();
        assert_eq!(e.path, "blocks[0].c[2][0][1].c", "{bad}");
    }
    // the contents before the tag
    let early = table("null").replace(
        r#"{"t":"ColWidth","c":null}"#,
        r#"{"c":"+inf","t":"ColWidth"}"#,
    );
    assert_eq!(width(&from_str(&early).unwrap()), f64::INFINITY);
}

#[test]
fn deeper_than_serde_json_reads() {
    // its 128 levels of JSON were 42 nested divs
    let doc = nested(DIV.0, DIV.1, 64);
    assert_eq!(to_string(&from_str(&doc).unwrap()), doc);
    // (contents before their tag wait for it, level by level: less deep)
    let early = nested(DIV_C_FIRST.0, DIV_C_FIRST.1, 16);
    assert_eq!(
        to_string(&from_str(&early).unwrap()),
        nested(DIV.0, DIV.1, 16)
    );
    // to MAX_DEPTH, where the stack allows (unoptimized, a level takes five
    // times the stack, and the reader stops before it runs out)
    let doc = nested(DIV.0, DIV.1, MAX_DEPTH - 1);
    match from_str(&doc) {
        Ok(read) => assert_eq!(to_string(&read), doc),
        Err(e) => {
            if !cfg!(debug_assertions) {
                panic!("{}", e.message);
            }
            assert!(
                e.message.starts_with("nested too deep for the stack"),
                "{}",
                e.message
            );
        }
    }
}

#[test]
fn too_deep_is_an_error() {
    let e = from_str(&nested(DIV.0, DIV.1, MAX_DEPTH)).unwrap_err();
    let expected = if cfg!(debug_assertions) {
        "nested too deep for the stack"
    } else {
        "nested more than 256 levels deep"
    };
    assert!(e.message.starts_with(expected), "{}", e.message);
    assert_eq!(e.path, "");
    // an error deeper than the reading for its path has stack for: the
    // error still, without the path
    let depth = if cfg!(debug_assertions) { 60 } else { 250 };
    let wrong = nested(DIV.0, DIV.1, depth).replace(r#""t":"Para","c":[]"#, r#""t":"Para","c":7"#);
    let e = from_str(&wrong).unwrap_err();
    assert!(e.message.starts_with("invalid type: integer `7`"), "{e}");
    // and never a crash, whatever takes the stack: on the megabyte of a
    // thread on Windows (or of a wasm module), tables in tables, unoptimized
    for (open, close) in [DIV, TABLE, DIV_C_FIRST] {
        for depth in [MAX_DEPTH - 1, 100_000] {
            let doc = nested(open, close, depth);
            std::thread::Builder::new()
                .stack_size(1 << 20)
                .spawn(move || match from_str(&doc) {
                    Ok(read) => {
                        // what was read can be written, copied, compared,
                        // walked, dropped
                        assert_eq!(to_string(&read).len(), doc.len());
                        assert!(read.clone() == read);
                        for order in [Order::Typewise, Order::Topdown, Order::Bottomup] {
                            apply(&mut read.clone(), &mut Every(order), None).unwrap();
                        }
                    }
                    Err(e) => assert!(e.message.starts_with("nested "), "{e}"),
                })
                .unwrap()
                .join()
                .unwrap();
        }
    }
}

#[test]
fn another_version_says_so() {
    // not where its JSON first differs from this version's
    let old =
        r#"{"pandoc-api-version":[1,20],"meta":{},"blocks":[{"t":"Table","c":[[],[],[],[],[]]}]}"#;
    let e = from_str(old).unwrap_err();
    assert_eq!(e.path, "pandoc-api-version");
    assert_eq!(e.message, "expected 1.23.*, got [1, 20]");
    let none = r#"{"meta":{},"blocks":[]}"#;
    assert!(
        from_str(none)
            .unwrap_err()
            .message
            .contains("pandoc-api-version")
    );
}

#[test]
fn many_replacements_in_a_long_list_take_no_longer() {
    // every other block deleted, the others doubled; every space deleted
    struct Thin(usize, Order);
    impl Filter for Thin {
        fn order(&self) -> Order {
            self.1
        }
        fn block(&mut self, x: &mut Block, _: &mut Ctx) -> Option<Vec<Block>> {
            self.0 += 1;
            match self.0 % 3 {
                0 => Some(vec![]),
                1 => Some(vec![x.clone(), Block::HorizontalRule]),
                _ => None,
            }
        }
        fn inline(&mut self, x: &mut Inline, _: &mut Ctx) -> Option<Vec<Inline>> {
            matches!(x, Inline::Space).then(Vec::new)
        }
    }
    let n: usize = 300_001;
    let make = || {
        let mut blocks: Vec<Block> = (0..n)
            .map(|i| Block::Para(vec![Inline::Str(i.to_string().into())]))
            .collect();
        blocks.push(Block::Para(
            (0..n)
                .flat_map(|_| [Inline::Str("a".into()), Inline::Space])
                .collect(),
        ));
        Pandoc::new(blocks)
    };
    let start = std::time::Instant::now();
    let mut results = Vec::new();
    for order in [Order::Typewise, Order::Topdown, Order::Bottomup] {
        let mut doc = make();
        apply(&mut doc, &mut Thin(0, order), None).unwrap();
        // a third deleted, a third kept, a third doubled
        assert_eq!(doc.blocks.len(), n + 1 - (n + 1) / 3 + (n + 1).div_ceil(3));
        assert_eq!(doc.blocks[0], Block::Para(vec![Inline::Str("0".into())]));
        assert_eq!(doc.blocks[1], Block::HorizontalRule);
        assert_eq!(doc.blocks[2], Block::Para(vec![Inline::Str("1".into())]));
        match doc.blocks.last() {
            Some(Block::Para(xs)) => assert_eq!(xs.len(), n),
            other => panic!("{other:?}"),
        }
        results.push(doc);
    }
    assert!(results[0] == results[1] && results[1] == results[2]);
    // (in place, each of these took seconds)
    assert!(start.elapsed().as_secs() < 20, "{:?}", start.elapsed());
}

/// Top-down, each Str becomes a span holding it, which is walked in turn:
/// as metavars with a field naming itself.
struct Runaway;

impl Filter for Runaway {
    fn order(&self) -> Order {
        Order::Topdown
    }
    fn inline(&mut self, x: &mut Inline, _: &mut Ctx) -> Option<Vec<Inline>> {
        matches!(x, Inline::Str(_)).then(|| {
            vec![
                panir::Span {
                    attr: Default::default(),
                    content: vec![x.clone()],
                }
                .into(),
            ]
        })
    }
}

#[test]
fn a_walk_without_end_is_an_error() {
    let mut doc = Pandoc::new(vec![Block::Para(panir::inlines("loop"))]);
    // (on a thread of the least stack Rust gives one, unoptimized)
    let e = std::thread::Builder::new()
        .stack_size(1 << 20)
        .spawn(move || apply(&mut doc, &mut Runaway, None).unwrap_err())
        .unwrap()
        .join()
        .unwrap();
    assert_eq!((e.function, e.node), (None, "Inlines"));
    let path = format!("blocks[0].content{}", "[0].content".repeat(MAX_DEPTH - 1));
    assert_eq!(e.path, path);
    assert_eq!(
        e.to_string(),
        format!(
            "nested more than 256 levels deep (a document that deep, or a top-down filter's \
             function whose result holds what it matched, walked without end)\n  walking the \
             Inlines at {path}"
        )
    );
    // the next walk starts from the top again
    apply(&mut divs(MAX_DEPTH - 1), &mut Every(Order::Topdown), None).unwrap();
}

/// Each Str's function walks a span holding it with the filter itself (a
/// walk started from a function, again and again).
struct Reentering;

impl Filter for Reentering {
    fn inline(&mut self, x: &mut Inline, ctx: &mut Ctx) -> Option<Vec<Inline>> {
        if !matches!(x, Inline::Str(_)) {
            return None;
        }
        let mut span = vec![Inline::Span(Box::new(panir::Span {
            attr: Default::default(),
            content: vec![x.clone()],
        }))];
        match apply(&mut span, self, None) {
            Ok(()) => Some(span),
            Err(e) => ctx.fail(e),
        }
    }
}

#[test]
fn walks_started_by_functions_count_from_where_they_are() {
    let mut doc = Pandoc::new(vec![Block::Para(panir::inlines("loop"))]);
    let e = std::thread::Builder::new()
        .stack_size(4 << 20)
        .spawn(move || apply(&mut doc, &mut Reentering, None).unwrap_err())
        .unwrap()
        .join()
        .unwrap();
    // the outermost function's error holds the inner ones, down to the walk
    // too deep
    assert_eq!(
        (e.function, e.path.as_str()),
        (Some("inline"), "blocks[0].content[0]")
    );
    assert!(
        e.to_string()
            .starts_with("nested more than 256 levels deep"),
        "{e}"
    );
    // and a panic in one leaves the depth as it was
    let panicked = std::panic::catch_unwind(|| {
        struct Panics;
        impl Filter for Panics {
            fn inline(&mut self, _: &mut Inline, _: &mut Ctx) -> Option<Vec<Inline>> {
                panic!("here")
            }
        }
        let mut deep = Pandoc::new(vec![Block::Para(panir::inlines("x"))]);
        apply(&mut deep, &mut Panics, None)
    });
    assert!(panicked.is_err());
    apply(&mut divs(MAX_DEPTH - 1), &mut Every(Order::Topdown), None).unwrap();
}

/// `depth` divs in one another, around a paragraph, and the walk's limit.
fn divs(depth: usize) -> Pandoc {
    let mut block = Block::Para(panir::inlines("x"));
    for _ in 1..depth {
        block = panir::Div {
            attr: Default::default(),
            content: vec![block],
        }
        .into();
    }
    Pandoc::new(vec![block])
}

#[test]
fn a_document_as_deep_as_one_read_is_walked_deeper_not() {
    // MAX_DEPTH levels (the Str the last), the most reading takes
    // (optimized: unoptimized, its stack runs out first)
    let mut doc = divs(MAX_DEPTH - 1);
    if !cfg!(debug_assertions) {
        assert!(from_str(&to_string(&doc)).is_ok());
        assert!(from_str(&to_string(&divs(MAX_DEPTH))).is_err());
    }
    for order in [Order::Typewise, Order::Topdown, Order::Bottomup] {
        apply(&mut doc, &mut Every(order), None).unwrap();
        let e = apply(&mut divs(MAX_DEPTH), &mut Every(order), None).unwrap_err();
        assert_eq!((e.function, e.node), (None, "Inlines"));
    }
}
