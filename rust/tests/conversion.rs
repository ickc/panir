use panir::{Block, Conversion, Ctx, Filter, Pandoc, apply, apply_with, inlines};

/// Notes what its `pandoc` function is told.
#[derive(Default)]
struct Seen(Option<(Option<String>, Conversion)>);

impl Filter for Seen {
    fn pandoc(&mut self, _: &mut Pandoc, ctx: &mut Ctx) -> Option<()> {
        self.0 = Some((ctx.format().map(str::to_owned), ctx.conversion().clone()));
        Some(())
    }
}

fn doc() -> Pandoc {
    Pandoc::new(vec![Block::Para(inlines("x"))])
}

#[test]
fn the_conversion() {
    let c = Conversion {
        format: Some("html5".into()),
        input_format: Some("commonmark_x-smart".into()),
        ..Conversion::default()
    };
    let mut seen = Seen::default();
    apply_with(&mut doc(), &mut seen, &c).unwrap();
    assert_eq!(seen.0, Some((Some("html5".into()), c)));

    // a format alone is a conversion knowing only that
    let mut seen = Seen::default();
    apply(&mut doc(), &mut seen, Some("latex")).unwrap();
    let (format, c) = seen.0.unwrap();
    assert_eq!(format.as_deref(), Some("latex"));
    assert_eq!(c.input_format, None);
}

#[test]
fn from_the_environment() {
    let env = |k: &str| match k {
        "PANDOC_READER_OPTIONS" => Some(r#"{"columns":72}"#.to_owned()),
        "PANDOC_INPUT_FORMAT" => Some("markdown+smart".to_owned()),
        "PANDOC_OUTPUT_FORMAT" => Some(String::new()),
        _ => None,
    };
    let c = Conversion::from_parts(Some("html5".into()), env);
    assert_eq!(c.format.as_deref(), Some("html5"));
    assert_eq!(c.input_format.as_deref(), Some("markdown+smart"));
    assert_eq!(c.output_format, None);
    assert_eq!(c.reader_options.unwrap()["columns"], 72);
    assert_eq!(c.writer_options, None); // (pandoc gives a JSON filter none)
    // as proposed for pandoc and libpandoc: Lua's PANDOC_WRITER_OPTIONS, keyed as the reader's
    let c = Conversion::from_parts(None, |k| {
        (k == "PANDOC_WRITER_OPTIONS").then(|| r#"{"number-sections":true}"#.to_owned())
    });
    assert_eq!(c.writer_options.unwrap()["number-sections"], true);
}

/// JSON after the document is an error, not a panic (found by fuzzing).
#[test]
fn trailing_characters() {
    let e = panir::from_str(r#"{"pandoc-api-version":[1,23,1,2],"meta":{},"blocks":[]} x"#)
        .unwrap_err();
    assert!(e.to_string().contains("trailing"), "{e}");
}

/// Notes, for each Str, whether its context had the metadata (its `title`).
struct MetaSeen(panir::Order, Vec<String>);

impl Filter for MetaSeen {
    fn order(&self) -> panir::Order {
        self.0
    }
    fn inline(&mut self, x: &mut panir::Inline, ctx: &mut Ctx) -> Option<Vec<panir::Inline>> {
        if let panir::Inline::Str(s) = x {
            let fields = ctx
                .meta()
                .map(|m| m.keys().map(|k| k.as_str()).collect::<Vec<_>>());
            self.1.push(format!(
                "{s}:{}",
                fields.map_or("-".into(), |f| f.join(","))
            ));
        }
        None
    }
}

#[test]
fn the_metadata_in_the_context() {
    use panir::{MetaValue, Order};
    for order in [Order::Typewise, Order::Topdown, Order::Bottomup] {
        let mut d = Pandoc::new(vec![Block::Para(inlines("a b"))]);
        d.meta
            .insert("title".into(), MetaValue::MetaInlines(inlines("T")));
        d.meta
            .insert("x".into(), MetaValue::MetaInlines(inlines("X")));
        let mut seen = MetaSeen(order, vec![]);
        apply(&mut d, &mut seen, None).unwrap();
        // a metadata value's Strs: the other fields (it is out of the map
        // while it is walked); the blocks': all
        assert_eq!(
            seen.1,
            ["T:x", "X:title", "a:title,x", "b:title,x"],
            "{order:?}"
        );
        assert_eq!(d.meta.len(), 2);
        // a fragment has no document
        let mut seen = MetaSeen(order, vec![]);
        apply(&mut d.blocks, &mut seen, None).unwrap();
        assert_eq!(seen.1, ["a:-", "b:-"]);
    }
}
