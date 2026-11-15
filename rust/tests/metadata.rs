//! Metadata as pandoc's Lua filters read and write it: made from plain
//! values, Lua's truth, a lookup through maps, one value or a list, a value
//! as inlines or blocks; and pandoc.utils.to_roman_numeral.

use std::collections::BTreeMap;

use panir::{
    Block, Inline, Meta, MetaValue, blocks_of, given, inlines, inlines_of, into_items, items,
    lookup, to_roman_numeral, truthy,
};

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
        assert!(truthy(&v), "{v:?}"); // a value in hand, as an option
    }
    assert!(!truthy(Some(&false.into())));
    assert!(!truthy(&MetaValue::from(false)));
    assert!(!truthy(None));
    // in a closure over values (an Option's filter gives a &&MetaValue)
    let mut meta = panir::Meta::new();
    meta.insert("off".into(), false.into());
    meta.insert("on".into(), "".into());
    assert_eq!(meta.values().filter(|v| truthy(*v)).count(), 1);
    assert_eq!(meta.get("on").filter(|v| truthy(*v)), Some(&"".into()));
}

#[test]
fn given_is_the_value_if_true() {
    let mut meta = panir::Meta::new();
    meta.insert("off".into(), false.into());
    meta.insert("on".into(), true.into());
    meta.insert("empty".into(), "".into());
    assert_eq!(given(meta.get("off")), None);
    assert_eq!(given(meta.get("missing")), None);
    assert_eq!(given(None), None);
    for k in ["on", "empty"] {
        assert!(std::ptr::eq(given(meta.get(k)).unwrap(), &meta[k])); // the document's own
        assert!(std::ptr::eq(given(&meta[k]).unwrap(), &meta[k])); // from a value too
    }
    // Lua's `meta.x or d`
    let d = MetaValue::from("d");
    assert_eq!(given(meta.get("off")).unwrap_or(&d), &d);
    assert_eq!(given(meta.get("empty")).unwrap_or(&d), &MetaValue::from(""));
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
    // from a value: in hand, perhaps missing (an Option of it, or of a reference)
    let value = &meta["toc"];
    assert_eq!(lookup(value, &["style", "name"]), Some(&"x".into()));
    assert_eq!(lookup(&meta.get("toc"), &["depth"]), Some(&"2".into()));
    assert_eq!(lookup(&meta.get("missing"), &["depth"]), None);
    assert_eq!(lookup(&meta["flag"], &["depth"]), None); // (not a map)
    assert_eq!(lookup(&meta["l"], &["a"]), None);
    // from a &mut Meta, as a meta function has it, and then changing it
    let m = &mut meta;
    assert!(lookup(m, &["flag"]).is_some());
    m.insert("more".into(), true.into());
    let taken = meta.remove("toc");
    assert_eq!(lookup(&taken, &["depth"]), Some(&"2".into()));
    assert_eq!(lookup(&None::<MetaValue>, &["depth"]), None);
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
    // a value in hand, as an option
    assert_eq!(items(&list), items(Some(&list)));
    assert_eq!(items(&one), [MetaValue::from("a")]);
    // owning: moved out of the document, an Option or a value
    let mut meta = panir::Meta::new();
    meta.insert("list".into(), list.clone());
    meta.insert("one".into(), one.clone());
    assert_eq!(into_items(meta.remove("list")), items(&list));
    assert_eq!(into_items(meta.remove("one")), items(&one));
    assert!(into_items(meta.remove("missing")).is_empty());
    assert_eq!(into_items(one.clone()), [one]);
    assert!(into_items(empty).is_empty());
    assert_eq!(into_items(MetaValue::from(false)), [false.into()]);
    assert!(meta.is_empty());
}

#[test]
fn inlines_of_and_blocks_of_as_lua() {
    // as pandoc 3.14's Lua converts a value where inlines or blocks go
    let plain = |s: &str| Block::Plain(inlines(s));
    let emph: MetaValue = vec![
        Inline::Str("x".into()),
        Inline::Space,
        Inline::Emph(inlines("y")),
    ]
    .into();
    let map = MetaValue::MetaMap(Meta::new());
    assert_eq!(inlines_of(&"a b".into()), Ok(inlines("a b")));
    assert_eq!(blocks_of(&"a b".into()), Ok(vec![plain("a b")]));
    assert_eq!(blocks_of(&"".into()), Ok(vec![plain("")]));
    let MetaValue::MetaInlines(xs) = &emph else {
        unreachable!()
    };
    assert_eq!(inlines_of(&emph).as_ref(), Ok(xs));
    let each: Vec<Block> = xs.iter().map(|x| Block::Plain(vec![x.clone()])).collect();
    assert_eq!(blocks_of(&emph), Ok(each));
    let para = MetaValue::from(Block::Para(inlines("p")));
    assert_eq!(blocks_of(&para), Ok(vec![Block::Para(inlines("p"))]));
    assert_eq!(
        (inlines_of(&map), blocks_of(&map)),
        (Ok(vec![]), Ok(vec![]))
    );
    let texts: MetaValue = ["a b", "c"].into_iter().collect();
    let strs = vec![Inline::Str("a b".into()), Inline::Str("c".into())];
    assert_eq!(inlines_of(&texts), Ok(strs)); // a Str each, unsplit
    assert_eq!(blocks_of(&texts), Ok(vec![plain("a b"), plain("c")]));
    let mixed: MetaValue = vec![
        MetaValue::from(Inline::Emph(inlines("x"))),
        map.clone(),
        MetaValue::MetaList(vec![]),
        MetaValue::MetaList(vec!["".into()]),
    ]
    .into();
    let got = vec![
        Block::Plain(vec![Inline::Emph(inlines("x"))]),
        plain(""),
        plain(""),
        Block::Plain(vec![Inline::Str("".into())]),
    ];
    assert_eq!(blocks_of(&mixed), Ok(got));
    let fails = [
        (
            inlines_of(&para).map(|_| ()),
            "expected inlines (MetaInlines, MetaString, MetaMap, or a MetaList of MetaStrings), got MetaBlocks",
        ),
        (inlines_of(&true.into()).map(|_| ()), "got MetaBool"),
        (
            inlines_of(&mixed).map(|_| ()),
            "MetaList.content[0]: expected an inline (a MetaString), got MetaInlines",
        ),
        (
            blocks_of(&false.into()).map(|_| ()),
            "expected blocks (MetaBlocks, MetaInlines, MetaString, MetaMap, or a MetaList), got MetaBool",
        ),
        (
            blocks_of(&vec![MetaValue::from("a"), para.clone()].into()).map(|_| ()),
            "MetaList.content[1]: expected a block",
        ),
        (
            blocks_of(&vec![MetaValue::MetaList(vec![emph.clone()])].into()).map(|_| ()),
            "MetaList.content[0].content[0]: expected an inline",
        ),
    ];
    for (got, message) in fails {
        let e = got.unwrap_err();
        assert!(e.contains(message), "{e}");
    }
}

#[test]
fn to_roman_numeral_as_lua() {
    // pandoc.utils.to_roman_numeral, checked with pandoc 3.14
    let got: Vec<String> = [1, 4, 9, 14, 40, 90, 400, 1994, 3999]
        .into_iter()
        .map(to_roman_numeral)
        .collect();
    assert_eq!(
        got,
        [
            "I",
            "IV",
            "IX",
            "XIV",
            "XL",
            "XC",
            "CD",
            "MCMXCIV",
            "MMMCMXCIX"
        ]
    );
    let out: Vec<String> = [0, 4000, i64::MAX, -1, i64::MIN]
        .into_iter()
        .map(to_roman_numeral)
        .collect();
    assert_eq!(out, ["", "?", "?", "?", "?"]);
}
