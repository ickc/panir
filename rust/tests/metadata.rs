//! Metadata as pandoc's Lua filters read and write it: made from plain
//! values, Lua's truth, a lookup through maps, one value or a list.

use std::collections::BTreeMap;

use panir::{Block, Inline, MetaValue};

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
