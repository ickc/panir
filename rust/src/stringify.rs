//! [`stringify`]: the text of a node, as pandoc's Lua `pandoc.utils.stringify`;
//! and [`to_roman_numeral`], as its `pandoc.utils.to_roman_numeral`.

use crate::{
    Block, Caption, Cell, Citation, Inline, Meta, MetaValue, Pandoc, QuoteType, Row, TableBody,
    TableFoot, TableHead, Visit, walk_inline_ref, walk_meta_value_ref,
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

/// An integer in Roman numerals, as pandoc's Lua
/// `pandoc.utils.to_roman_numeral`: 1 to 3999 (`"MMXXVI"`), `""` for 0, and
/// `"?"` for anything else, negative or 4000 and more.
///
/// ```
/// use panir::to_roman_numeral;
///
/// assert_eq!(to_roman_numeral(1994), "MCMXCIV");
/// assert_eq!([to_roman_numeral(0), to_roman_numeral(4000), to_roman_numeral(-1)], ["", "?", "?"]);
/// ```
pub fn to_roman_numeral(n: i64) -> String {
    #[rustfmt::skip]
    const ROMAN: [(i64, &str); 13] = [
        (1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"),
        (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"),
    ];
    if !(0..4000).contains(&n) {
        return "?".into();
    }
    let mut n = n;
    let mut out = String::new();
    for (value, letters) in ROMAN {
        out.push_str(&letters.repeat((n / value) as usize));
        n %= value;
    }
    out
}

/// A number as Lua's `tonumber(text)` reads one: decimal (`"12"`, `"1.5"`,
/// `".5"`, `"1e3"`) or a hexadecimal integer (`"0x1F"`), a sign before it,
/// ASCII blanks around it; `None` for anything else (`""`, `"1 2"`,
/// `"inf"`), where Lua's is `nil`. Not Lua's hexadecimal fractions and
/// exponents (`0x1.8p1`). For ports reading a number from an attribute or
/// the metadata as their Lua original did (`tonumber(el.attributes.n)`).
///
/// ```
/// use panir::lua_number;
///
/// assert_eq!([lua_number(" 12 "), lua_number("1e3"), lua_number("-0x10")], [Some(12.0), Some(1000.0), Some(-16.0)]);
/// assert_eq!([lua_number("1 2"), lua_number("inf"), lua_number("")], [None, None, None]);
/// ```
pub fn lua_number(text: &str) -> Option<f64> {
    let t = text.trim_matches([' ', '\t', '\n', '\x0b', '\x0c', '\r']);
    let (sign, digits) = match t.strip_prefix('-') {
        Some(rest) => (-1.0, rest),
        None => (1.0, t.strip_prefix('+').unwrap_or(t)),
    };
    let all_digits = |s: &str, radix| s.chars().all(|c| c.is_digit(radix));
    if let Some(hex) = digits
        .strip_prefix("0x")
        .or_else(|| digits.strip_prefix("0X"))
    {
        if hex.is_empty() {
            return None;
        }
        let n = hex
            .chars()
            .try_fold(0.0, |n, c| Some(n * 16.0 + f64::from(c.to_digit(16)?)))?;
        return Some(sign * n);
    }
    // digits, a point, digits (one at least of the two), an exponent;
    // checked first, as Rust reads more ("inf", "nan")
    let (mantissa, exponent) = match digits.split_once(['e', 'E']) {
        Some((m, e)) => (m, Some(e.strip_prefix(['+', '-']).unwrap_or(e))),
        None => (digits, None),
    };
    let (whole, fraction) = mantissa.split_once('.').unwrap_or((mantissa, ""));
    let decimal = !(whole.is_empty() && fraction.is_empty())
        && all_digits(whole, 10)
        && all_digits(fraction, 10)
        && exponent.is_none_or(|e| !e.is_empty() && all_digits(e, 10));
    decimal
        .then(|| digits.parse::<f64>().ok().map(|n| sign * n))
        .flatten()
}
