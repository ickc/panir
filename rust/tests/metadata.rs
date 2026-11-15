//! Metadata as pandoc's Lua filters read and write it: made from plain
//! values, Lua's truth, a lookup through maps, one value or a list.

use std::collections::BTreeMap;

use panir::{Block, Inline, MetaValue, items, lookup, truthy};

#[test]
fn metadata_from_plain_values() {
    // as pandoc's Lua converts plain values
    assert_eq!(MetaValue::from("a"), MetaValue::MetaString("a".into()));
    assert_eq!(
        MetaValue::from(String::from("a")),
        MetaValue::MetaString("a".into())
    );
    assert_eq!(MetaValue::from(true), MetaValue::MetaBool(true));
    assert_eq!(
        MetaValue::from(Inline::Space),
        MetaValue::MetaInlines(vec![Inline::Space])
    );
    assert_eq!(
        MetaValue::from(panir::inlines("a b")),
        MetaValue::MetaInlines(panir::inlines("a b"))
    );
    assert_eq!(
        MetaValue::from(Block::HorizontalRule),
        MetaValue::MetaBlocks(vec![Block::HorizontalRule])
    );
    assert_eq!(
        MetaValue::from(vec![MetaValue::from(false)]),
        MetaValue::MetaList(vec![MetaValue::MetaBool(false)])
    );
    // a map of lists of strings in one expression (cito's)
    let by_property = BTreeMap::from([("cites", vec!["a", "b"]), ("extends", vec![])]);
    let cites: MetaValue = by_property
        .into_iter()
        .map(|(p, keys)| (p, keys.into_iter().collect::<MetaValue>()))
        .collect();
    let expected = MetaValue::MetaMap(BTreeMap::from([
        (
            "cites".into(),
            MetaValue::MetaList(vec![
                MetaValue::MetaString("a".into()),
                MetaValue::MetaString("b".into()),
            ]),
        ),
        ("extends".into(), MetaValue::MetaList(vec![])),
    ]));
    assert_eq!(cites, expected);
    let m: panir::Meta = BTreeMap::from([("k".into(), true.into())]);
    assert_eq!(MetaValue::from(m.clone()), MetaValue::MetaMap(m));
}

#[test]
fn lua_truth() {
    // false only for a missing value and `false`, as Lua's `if meta.x then`
    for v in [
        MetaValue::from(true),
        "".into(),
        "false".into(),
        MetaValue::MetaInlines(vec![]),
        MetaValue::MetaBlocks(vec![]),
        MetaValue::MetaList(vec![]),
        MetaValue::MetaMap(BTreeMap::new()),
    ] {
        assert!(truthy(Some(&v)), "{v:?}");
    }
    assert!(!truthy(Some(&false.into())));
    assert!(!truthy(None));
}

#[test]
fn a_nested_option() {
    let mut meta = panir::Meta::new();
    let style: MetaValue = [("name", "x")].into_iter().collect();
    let toc: MetaValue = [("depth", MetaValue::from("2")), ("style", style)]
        .into_iter()
        .collect();
    meta.insert("toc".into(), toc);
    meta.insert("flag".into(), true.into());
    meta.insert("l".into(), ["a"].into_iter().collect());
    assert_eq!(lookup(&meta, &["toc", "depth"]), Some(&"2".into()));
    assert_eq!(lookup(&meta, &["toc", "style", "name"]), Some(&"x".into()));
    assert_eq!(lookup(&meta, &["toc"]), meta.get("toc"));
    for path in [
        &["toc", "missing"][..],
        &["missing", "depth"],
        &["flag", "x"],
        &["l", "a"],
        &["toc", "depth", "x"],
        &[],
    ] {
        assert_eq!(lookup(&meta, path), None, "{path:?}");
    }
    // from a MetaMap: its content is a Meta too
    let Some(MetaValue::MetaMap(toc)) = meta.get("toc") else {
        panic!()
    };
    assert_eq!(lookup(toc, &["depth"]), Some(&"2".into()));
}

#[test]
fn one_value_or_a_list() {
    let one = MetaValue::from("a");
    let list: MetaValue = ["a", "b"].into_iter().collect();
    let empty = MetaValue::MetaList(vec![]);
    assert_eq!(items(Some(&one)), [MetaValue::from("a")]);
    assert_eq!(items(Some(&list)), ["a".into(), "b".into()]);
    assert!(items(Some(&empty)).is_empty());
    assert_eq!(items(Some(&false.into())), [false.into()]);
    assert!(items(None).is_empty());
}
