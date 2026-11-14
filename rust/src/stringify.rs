//! [`stringify`]: the text of a node, as pandoc's Lua `pandoc.utils.stringify`.

use crate::{
    walk_inline_ref, walk_meta_value_ref, Block, Caption, Cell, Citation, Inline, Meta, MetaValue,
    Pandoc, QuoteType, Row, TableBody, TableFoot, TableHead, Visit,
};

/// The text of a node (or of a list of them), without markup, exactly as
/// pandoc's Lua `pandoc.utils.stringify`.
///
/// Spaces and breaks become `" "`, quotes curly quotes, notes and a
/// citation's data are left out, code and math keep their text, a raw
/// `html` `<br>` is a space. Metadata: a map's values in the order of
/// their keys, a `MetaBool` `"true"` or `"false"`, a `MetaString` its
/// text; but a whole document is the text of its inlines only (in its
/// metadata too: not its `MetaString` and `MetaBool` values).
///
/// ```
/// use panir::{stringify, Inline};
///
/// let x = Inline::Emph(vec!["a".into(), Inline::SoftBreak, "b".into()]);
/// assert_eq!(stringify(&x), "a b");
/// assert_eq!(stringify(&panir::inlines("one  two")), "one two");
/// ```
pub fn stringify<T: Stringify + ?Sized>(x: &T) -> String {
    let mut out = String::new();
    x.stringify_into(&mut out);
    out
}

/// What [`stringify()`] takes: a document, its metadata, a block, an inline,
/// a metadata value, a citation, a table's parts, or a list of any of them.
pub trait Stringify {
    /// Appends the text to `out`.
    fn stringify_into(&self, out: &mut String);
}

/// A walk gathering text: pandoc's `Shared.stringify` (`inlines_only`), and
/// its Lua's for metadata.
struct Plain<'a> {
    out: &'a mut String,
    inlines_only: bool,
}

impl Visit for Plain<'_> {
    fn visit_inline(&mut self, x: &Inline) {
        match x {
            Inline::Str(s) => self.out.push_str(s),
            Inline::Space | Inline::SoftBreak | Inline::LineBreak => self.out.push(' '),
            Inline::Code(c) => self.out.push_str(&c.text),
            Inline::Math(m) => self.out.push_str(&m.text),
            Inline::RawInline(r) if r.format == "html" && r.text.starts_with("<br") => {
                self.out.push(' ')
            }
            Inline::Note(_) => {}
            Inline::Cite(c) => self.visit_inlines(&c.content),
            Inline::Quoted(q) => {
                let (open, close) = match q.quote_type {
                    QuoteType::SingleQuote => ('\u{2018}', '\u{2019}'),
                    QuoteType::DoubleQuote => ('\u{201c}', '\u{201d}'),
                };
                self.out.push(open);
                self.visit_inlines(&q.content);
                self.out.push(close);
            }
            _ => walk_inline_ref(self, x),
        }
    }

    fn visit_meta_value(&mut self, x: &MetaValue) {
        match x {
            MetaValue::MetaString(s) if !self.inlines_only => self.out.push_str(s),
            MetaValue::MetaBool(b) if !self.inlines_only => {
                self.out.push_str(if *b { "true" } else { "false" })
            }
            _ => walk_meta_value_ref(self, x),
        }
    }
}

/// `$t`'s text through `Plain`'s `$visit`.
macro_rules! stringify_with {
    ($($t:ty => $visit:ident),* $(,)?) => {$(
        impl Stringify for $t {
            fn stringify_into(&self, out: &mut String) {
                Plain { out, inlines_only: false }.$visit(self);
            }
        }
    )*};
}

stringify_with!(
    Inline => visit_inline,
    Block => visit_block,
    MetaValue => visit_meta_value,
    Caption => visit_caption,
    TableHead => visit_table_head,
    TableBody => visit_table_body,
    TableFoot => visit_table_foot,
    Row => visit_row,
    Cell => visit_cell,
    Citation => visit_citation,
);

impl Stringify for Pandoc {
    /// The text of its inlines, in the metadata (in the order of its keys)
    /// and the blocks: pandoc's `stringify` of a document.
    fn stringify_into(&self, out: &mut String) {
        Plain {
            out,
            inlines_only: true,
        }
        .visit_pandoc(self);
    }
}

impl Stringify for Meta {
    /// Its values', in the order of their keys.
    fn stringify_into(&self, out: &mut String) {
        self.values().for_each(|v| v.stringify_into(out));
    }
}

impl<T: Stringify> Stringify for [T] {
    fn stringify_into(&self, out: &mut String) {
        self.iter().for_each(|x| x.stringify_into(out));
    }
}

impl<T: Stringify> Stringify for Vec<T> {
    fn stringify_into(&self, out: &mut String) {
        self.as_slice().stringify_into(out);
    }
}
