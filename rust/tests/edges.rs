//! The edges: what a review and fuzzing against pandoc found (fuzz/).

use panir::{from_str, to_string, Block, ColSpec, ColWidth, Pandoc, Table, MAX_DEPTH};

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
        let made = Pandoc::new(vec![Table {
            col_specs: vec![ColSpec {
                width: ColWidth::ColWidth(value),
                ..Default::default()
            }],
            ..Default::default()
        }
        .into()]);
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
                        // what was read can be written, copied, compared, dropped
                        assert_eq!(to_string(&read).len(), doc.len());
                        assert!(read.clone() == read);
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
    assert!(from_str(none)
        .unwrap_err()
        .message
        .contains("pandoc-api-version"));
}

#[test]
fn many_replacements_in_a_long_list_take_no_longer() {
    use panir::{apply, Ctx, Filter, Inline, Order};
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
        apply(&mut doc, &mut Thin(0, order), None);
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
