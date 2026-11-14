//! Metadata made from plain Rust values, as pandoc's Lua converts plain Lua
//! values.

use crate::{Block, Inline, Meta, MetaValue, Text};

// Plain values as metadata, as pandoc's Lua converts them: a string is a
// MetaString, a boolean a MetaBool, inlines MetaInlines, blocks MetaBlocks,
// metadata values a MetaList, a map a MetaMap.

impl From<&str> for MetaValue {
    fn from(s: &str) -> Self {
        MetaValue::MetaString(s.into())
    }
}

impl From<String> for MetaValue {
    fn from(s: String) -> Self {
        MetaValue::MetaString(s.into())
    }
}

impl From<Text> for MetaValue {
    fn from(s: Text) -> Self {
        MetaValue::MetaString(s)
    }
}

impl From<bool> for MetaValue {
    fn from(b: bool) -> Self {
        MetaValue::MetaBool(b)
    }
}

impl From<Inline> for MetaValue {
    fn from(x: Inline) -> Self {
        MetaValue::MetaInlines(vec![x])
    }
}

impl From<Vec<Inline>> for MetaValue {
    fn from(xs: Vec<Inline>) -> Self {
        MetaValue::MetaInlines(xs)
    }
}

impl From<Block> for MetaValue {
    fn from(x: Block) -> Self {
        MetaValue::MetaBlocks(vec![x])
    }
}

impl From<Vec<Block>> for MetaValue {
    fn from(xs: Vec<Block>) -> Self {
        MetaValue::MetaBlocks(xs)
    }
}

impl From<Vec<MetaValue>> for MetaValue {
    fn from(xs: Vec<MetaValue>) -> Self {
        MetaValue::MetaList(xs)
    }
}

impl From<Meta> for MetaValue {
    fn from(m: Meta) -> Self {
        MetaValue::MetaMap(m)
    }
}

/// A `MetaList` of what converts to metadata: `["a", "b"].into_iter().collect()`.
impl<T: Into<MetaValue>> FromIterator<T> for MetaValue {
    fn from_iter<I: IntoIterator<Item = T>>(iter: I) -> Self {
        MetaValue::MetaList(iter.into_iter().map(Into::into).collect())
    }
}

/// A `MetaMap` of keys and what converts to metadata: `[("k", "v")].into_iter().collect()`.
impl<K: Into<Text>, V: Into<MetaValue>> FromIterator<(K, V)> for MetaValue {
    fn from_iter<I: IntoIterator<Item = (K, V)>>(iter: I) -> Self {
        MetaValue::MetaMap(
            iter.into_iter()
                .map(|(k, v)| (k.into(), v.into()))
                .collect(),
        )
    }
}
