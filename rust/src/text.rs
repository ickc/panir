//! [`Text`]: the AST's string (pandoc-types' `Text`), which keeps short
//! strings in place of a heap pointer.

use std::borrow::{Borrow, Cow};
use std::cmp::Ordering;
use std::fmt;
use std::hash::{Hash, Hasher};
use std::ops::Deref;

use serde::de::{self, Deserialize, Deserializer, Visitor};
use serde::{Serialize, Serializer};

/// The longest string kept inline: 22 bytes, so a `Text` is 24 bytes, as a
/// `String` is (and `Inline` and `Block` stay 32).
const INLINE: usize = 22;

/// A string of the AST: every `Str`, `Code`, URL, identifier, class...
///
/// It reads as a `&str` (`Deref`), and is made from `&str`, `String`,
/// `char` or an iterator of them: `*s = s.to_uppercase().into()`. In place,
/// [`push_str`](Text::push_str) appends (amortized O(1), as a `String`'s),
/// and [`make_mut`](Text::make_mut) gives a `&mut String`.
///
/// Up to 22 bytes (99.7% of the words of pandoc's changelog) are kept in
/// the value itself, without a heap allocation; longer strings are a
/// `String` on the heap, behind a `Box` (8 bytes, so a `Text` stays 24).
///
/// ```
/// use panir::Text;
///
/// let mut t = Text::from("word");
/// assert_eq!(t, "word");
/// assert_eq!(t.len(), 4);
/// t.push_str("s");
/// assert_eq!(t, "words");
/// assert_eq!(std::mem::size_of::<Text>(), 24);
/// ```
#[derive(Clone)]
pub struct Text(Repr);

#[derive(Clone)]
enum Repr {
    Inline {
        len: u8,
        buf: [u8; INLINE],
    },
    // boxed: 8 bytes, so that the whole is 24 (a `String` is 24 itself)
    #[allow(clippy::box_collection)]
    Heap(Box<String>),
}

impl Text {
    /// The empty string.
    #[inline]
    pub const fn new() -> Self {
        Text(Repr::Inline {
            len: 0,
            buf: [0; INLINE],
        })
    }

    #[inline]
    pub fn as_str(&self) -> &str {
        match &self.0 {
            // Always valid UTF-8: only ever copied whole from a `str`. The
            // check costs little at 22 bytes or less; the fallback is never
            // taken.
            Repr::Inline { len, buf } => std::str::from_utf8(&buf[..*len as usize]).unwrap_or(""),
            Repr::Heap(s) => s,
        }
    }

    /// The string's bytes (UTF-8).
    #[inline]
    pub fn as_bytes(&self) -> &[u8] {
        match &self.0 {
            Repr::Inline { len, buf } => &buf[..*len as usize],
            Repr::Heap(s) => s.as_bytes(),
        }
    }

    /// Its length in bytes.
    #[inline]
    pub fn len(&self) -> usize {
        match &self.0 {
            Repr::Inline { len, .. } => *len as usize,
            Repr::Heap(s) => s.len(),
        }
    }

    #[inline]
    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }

    pub fn into_string(self) -> String {
        match self.0 {
            Repr::Inline { .. } => self.as_str().to_owned(),
            Repr::Heap(s) => *s,
        }
    }

    /// Append `s`: in place while it fits in 22 bytes, then on the heap,
    /// growing as a `String` does.
    pub fn push_str(&mut self, s: &str) {
        match &mut self.0 {
            Repr::Inline { len, buf } if *len as usize + s.len() <= INLINE => {
                let n = *len as usize;
                buf[n..n + s.len()].copy_from_slice(s.as_bytes());
                *len += s.len() as u8;
            }
            Repr::Heap(h) => h.push_str(s),
            Repr::Inline { .. } => {
                // room to go on appending, as a String that grew would have
                let mut h = String::with_capacity((self.len() + s.len()).max(2 * INLINE));
                h.push_str(self.as_str());
                h.push_str(s);
                self.0 = Repr::Heap(Box::new(h));
            }
        }
    }

    pub fn push(&mut self, c: char) {
        self.push_str(c.encode_utf8(&mut [0; 4]));
    }

    /// The string as a `String`, to change in place (moved to the heap if it
    /// was short).
    pub fn make_mut(&mut self) -> &mut String {
        if let Repr::Inline { .. } = self.0 {
            self.0 = Repr::Heap(Box::new(self.as_str().to_owned()));
        }
        match &mut self.0 {
            Repr::Heap(h) => h,
            Repr::Inline { .. } => unreachable!("just moved to the heap"),
        }
    }
}

/// `dst[..src.len()] = src` for up to 22 bytes, as two fixed-size copies
/// that overlap (fixed sizes compile to plain moves). A `copy_from_slice` of
/// a variable length calls `memcpy`, unless optimized for speed: at
/// opt-level "s" (as wasm filters are built) that made reading JSON 8%
/// slower than with `String`.
#[inline]
fn copy_small(dst: &mut [u8; INLINE], src: &[u8]) {
    let n = src.len();
    if n >= 16 {
        dst[..16].copy_from_slice(&src[..16]);
        dst[n - 16..n].copy_from_slice(&src[n - 16..]);
    } else if n >= 8 {
        dst[..8].copy_from_slice(&src[..8]);
        dst[n - 8..n].copy_from_slice(&src[n - 8..]);
    } else if n >= 4 {
        dst[..4].copy_from_slice(&src[..4]);
        dst[n - 4..n].copy_from_slice(&src[n - 4..]);
    } else if n > 0 {
        dst[0] = src[0];
        dst[n / 2] = src[n / 2];
        dst[n - 1] = src[n - 1];
    }
}

impl Default for Text {
    fn default() -> Self {
        Text::new()
    }
}

impl Deref for Text {
    type Target = str;
    #[inline]
    fn deref(&self) -> &str {
        self.as_str()
    }
}

impl AsRef<str> for Text {
    fn as_ref(&self) -> &str {
        self.as_str()
    }
}

impl Borrow<str> for Text {
    fn borrow(&self) -> &str {
        self.as_str()
    }
}

impl From<&str> for Text {
    #[inline]
    fn from(s: &str) -> Self {
        if s.len() <= INLINE {
            // into the result itself: through a local array, the bytes were
            // stored narrowly and moved widely, which stalls the CPU
            let mut t = Text::new();
            if let Repr::Inline { len, buf } = &mut t.0 {
                copy_small(buf, s.as_bytes());
                *len = s.len() as u8;
            }
            t
        } else {
            Text(Repr::Heap(Box::new(s.to_owned())))
        }
    }
}

impl From<String> for Text {
    fn from(s: String) -> Self {
        if s.len() <= INLINE {
            Text::from(s.as_str())
        } else {
            Text(Repr::Heap(Box::new(s)))
        }
    }
}

impl From<&String> for Text {
    fn from(s: &String) -> Self {
        Text::from(s.as_str())
    }
}

impl From<Box<str>> for Text {
    fn from(s: Box<str>) -> Self {
        Text::from(s.into_string())
    }
}

impl From<Cow<'_, str>> for Text {
    fn from(s: Cow<'_, str>) -> Self {
        match s {
            Cow::Borrowed(s) => Text::from(s),
            Cow::Owned(s) => Text::from(s),
        }
    }
}

impl From<char> for Text {
    fn from(c: char) -> Self {
        Text::from(c.encode_utf8(&mut [0; 4]) as &str)
    }
}

impl From<&Text> for Text {
    fn from(s: &Text) -> Self {
        s.clone()
    }
}

impl From<Text> for String {
    fn from(s: Text) -> Self {
        s.into_string()
    }
}

impl<T> FromIterator<T> for Text
where
    String: FromIterator<T>,
{
    fn from_iter<I: IntoIterator<Item = T>>(iter: I) -> Self {
        String::from_iter(iter).into()
    }
}

/// Appends, as a `String` does: `t.extend(["a", "b"])`, `t.extend(s.chars())`.
impl Extend<char> for Text {
    fn extend<I: IntoIterator<Item = char>>(&mut self, iter: I) {
        iter.into_iter().for_each(|c| self.push(c));
    }
}

impl<'a> Extend<&'a char> for Text {
    fn extend<I: IntoIterator<Item = &'a char>>(&mut self, iter: I) {
        iter.into_iter().for_each(|c| self.push(*c));
    }
}

macro_rules! extend_str {
    ($($t:ty),*) => {$(
        impl<'a> Extend<$t> for Text {
            fn extend<I: IntoIterator<Item = $t>>(&mut self, iter: I) {
                iter.into_iter().for_each(|s| self.push_str(&s));
            }
        }
    )*};
}

extend_str!(&'a str, String, Text, Box<str>, Cow<'a, str>);

/// `write!(t, "{n} words")` appends.
impl fmt::Write for Text {
    fn write_str(&mut self, s: &str) -> fmt::Result {
        self.push_str(s);
        Ok(())
    }
}

impl std::str::FromStr for Text {
    type Err = std::convert::Infallible;
    fn from_str(s: &str) -> Result<Self, Self::Err> {
        Ok(s.into())
    }
}

impl fmt::Debug for Text {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        fmt::Debug::fmt(self.as_str(), f)
    }
}

impl fmt::Display for Text {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        fmt::Display::fmt(self.as_str(), f)
    }
}

// By the bytes, which compare as the strings do: `as_str` checks a short
// string's UTF-8 each time, half of the time of comparing two documents.
impl PartialEq for Text {
    #[inline]
    fn eq(&self, other: &Text) -> bool {
        self.as_bytes() == other.as_bytes()
    }
}

impl Eq for Text {}

impl PartialOrd for Text {
    fn partial_cmp(&self, other: &Text) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}

impl Ord for Text {
    fn cmp(&self, other: &Text) -> Ordering {
        self.as_bytes().cmp(other.as_bytes())
    }
}

impl Hash for Text {
    fn hash<H: Hasher>(&self, h: &mut H) {
        self.as_str().hash(h)
    }
}

macro_rules! eq_str {
    ($($t:ty),*) => {$(
        impl PartialEq<$t> for Text {
            fn eq(&self, other: &$t) -> bool {
                self.as_bytes() == other.as_bytes()
            }
        }
        impl PartialEq<Text> for $t {
            fn eq(&self, other: &Text) -> bool {
                self.as_bytes() == other.as_bytes()
            }
        }
    )*};
}

eq_str!(str, &str, String);

impl Serialize for Text {
    fn serialize<S: Serializer>(&self, s: S) -> Result<S::Ok, S::Error> {
        s.serialize_str(self.as_str())
    }
}

impl<'de> Deserialize<'de> for Text {
    fn deserialize<D: Deserializer<'de>>(d: D) -> Result<Self, D::Error> {
        struct V;
        impl Visitor<'_> for V {
            type Value = Text;
            fn expecting(&self, f: &mut fmt::Formatter) -> fmt::Result {
                f.write_str("a string")
            }
            // serde_json hands over a `&str` without allocating when the
            // JSON string has no escapes: a short one is then never on the heap.
            #[inline]
            fn visit_str<E: de::Error>(self, s: &str) -> Result<Text, E> {
                Ok(s.into())
            }
            fn visit_string<E: de::Error>(self, s: String) -> Result<Text, E> {
                Ok(s.into())
            }
        }
        d.deserialize_str(V)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn inline_and_heap() {
        for s in [
            "",
            "a",
            "héllo",
            &"x".repeat(22),
            &"x".repeat(23),
            "日本語のテキスト、長め",
        ] {
            let t = Text::from(s);
            assert_eq!(t, s);
            assert_eq!(Text::from(s.to_owned()), s);
            assert_eq!(t.clone().into_string(), s);
            let inline = matches!(t.0, Repr::Inline { .. });
            assert_eq!(inline, s.len() <= INLINE, "{s}");
        }
        assert_eq!(std::mem::size_of::<Text>(), 24);
    }

    #[test]
    fn order_and_json() {
        let (a, b) = (Text::from("a"), Text::from("b".repeat(30)));
        assert!(a < b);
        let t: Text = serde_json::from_str(r#""a\"b""#).unwrap();
        assert_eq!(t, "a\"b");
        assert_eq!(serde_json::to_string(&t).unwrap(), r#""a\"b""#);
        assert_eq!("ab".chars().collect::<Text>(), "ab");
    }

    #[test]
    fn from_cow_extend_write() {
        use std::fmt::Write as _;
        assert_eq!(Text::from(Cow::Borrowed("a")), "a");
        assert_eq!(
            Text::from(Cow::<str>::Owned("b".repeat(30))),
            "b".repeat(30)
        );
        let mut t = Text::from("x");
        t.extend(['y', 'z']);
        t.extend(&['!']);
        t.extend(["ab", "cd"]);
        t.extend([String::from("e"), "f".repeat(20)]);
        t.extend([Text::from("g")]);
        t.extend([Cow::Borrowed("h")]);
        t.extend([Box::<str>::from("i")]);
        write!(t, "{}-{}", 1, 2).unwrap();
        assert_eq!(t, format!("xyz!abcde{}ghi1-2", "f".repeat(20)));
    }

    #[test]
    fn every_short_length() {
        let all = "abcdefghijklmnopqrstuvwxyz";
        for n in 0..=INLINE {
            assert_eq!(Text::from(&all[..n]), &all[..n]);
        }
    }

    #[test]
    fn push() {
        let mut t = Text::new();
        let mut s = String::new();
        for i in 0..100 {
            let piece = if i % 3 == 0 { "é" } else { "ab" };
            t.push_str(piece);
            s.push_str(piece);
            assert_eq!(t, s);
            assert_eq!(matches!(t.0, Repr::Inline { .. }), s.len() <= INLINE);
        }
        t.push('!');
        t.make_mut().push('?');
        assert!(t.ends_with("!?"));
        let mut short = Text::from("a");
        short.make_mut().push('b');
        assert_eq!(short, "ab");
    }
}
