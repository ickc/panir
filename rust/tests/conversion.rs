use panir::{apply, apply_with, inlines, Block, Conversion, Ctx, Filter, Pandoc, Typewise};

/// Notes what its `pandoc` function is told.
#[derive(Default)]
struct Seen(Option<(Option<String>, Conversion)>);

impl Filter for Seen {
    type Order = Typewise;
    fn pandoc(&mut self, _: &mut Pandoc, ctx: &mut Ctx<Typewise>) {
        self.0 = Some((ctx.format().map(str::to_owned), ctx.conversion().clone()));
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
    apply_with(&mut doc(), &mut seen, &c);
    assert_eq!(seen.0, Some((Some("html5".into()), c)));

    // a format alone is a conversion knowing only that
    let mut seen = Seen::default();
    apply(&mut doc(), &mut seen, Some("latex"));
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
}

/// JSON after the document is an error, not a panic (found by fuzzing).
#[test]
fn trailing_characters() {
    let e = panir::from_str(r#"{"pandoc-api-version":[1,23,1,2],"meta":{},"blocks":[]} x"#)
        .unwrap_err();
    assert!(e.to_string().contains("trailing"), "{e}");
}
