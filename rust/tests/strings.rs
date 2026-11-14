//! Strings convert as in pandoc's Lua.

use panir::{Block, Inline, blocks, inlines};

#[test]
fn inlines_are_words_and_spaces() {
    assert_eq!(
        inlines(" a  b\nc\t"),
        vec![
            Inline::Space,
            "a".into(),
            Inline::Space,
            "b".into(),
            Inline::SoftBreak,
            "c".into(),
            Inline::Space,
        ]
    );
    assert_eq!(inlines(""), vec![]);
    assert_eq!(
        inlines("a\r\nb"),
        vec!["a".into(), Inline::SoftBreak, "b".into()]
    );
    assert_eq!(
        inlines("héllo wörld"),
        vec!["héllo".into(), Inline::Space, "wörld".into()]
    );
}

#[test]
fn one_string_is_one_node() {
    assert_eq!(
        Inline::from("hello world"),
        Inline::Str("hello world".into())
    );
    assert_eq!(
        Block::from("hello world"),
        Block::Plain(vec!["hello".into(), Inline::Space, "world".into()])
    );
    assert_eq!(blocks("x"), vec![Block::Plain(vec!["x".into()])]);
}
