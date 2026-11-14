//! An element's attributes by key, as pandoc's Lua `el.attributes[key]`.

use crate::{Attr, Text};

/// By key, as pandoc's Lua `el.attributes[key]`: the attributes are pairs,
/// in order, a key perhaps in two; the first pair with a key is the one
/// read, set and removed.
///
/// ```
/// use panir::Attr;
///
/// let mut attr = Attr {
///     attributes: vec![("k".into(), "1".into()), ("j".into(), "2".into()), ("k".into(), "3".into())],
///     ..Attr::default()
/// };
/// assert_eq!(attr.get("k"), Some("1"));
/// attr.set("k", "new"); // the first pair's value, in its place
/// attr.set("added", "4"); // a new key: at the end
/// assert_eq!(attr.remove("k").as_deref(), Some("new"));
/// assert_eq!(attr.get("k"), Some("3"));
/// assert_eq!(attr.attributes.len(), 3);
/// ```
impl Attr {
    /// An `Attr`, in the order of pandoc's Lua `pandoc.Attr(identifier,
    /// classes, attributes)`.
    ///
    /// ```
    /// use panir::{Attr, Inline, Span};
    ///
    /// let field = "city";
    /// let span: Inline = Span {
    ///     attr: Attr::new("", &["var"], &[("field", field)]),
    ///     content: vec!["Paris".into()],
    /// }
    /// .into();
    /// # let _ = span;
    /// ```
    pub fn new(identifier: &str, classes: &[&str], attributes: &[(&str, &str)]) -> Self {
        Attr {
            identifier: identifier.into(),
            classes: classes.iter().map(|&c| c.into()).collect(),
            attributes: attributes
                .iter()
                .map(|&(k, v)| (k.into(), v.into()))
                .collect(),
        }
    }

    /// The value of the first pair with `key`.
    pub fn get(&self, key: &str) -> Option<&str> {
        self.attributes
            .iter()
            .find(|(k, _)| k == key)
            .map(|(_, v)| v.as_str())
    }

    /// Give the first pair with `key` the value, in its place; else add a
    /// pair at the end.
    pub fn set(&mut self, key: &str, value: impl Into<Text>) {
        match self.attributes.iter_mut().find(|(k, _)| k == key) {
            Some((_, v)) => *v = value.into(),
            None => self.attributes.push((key.into(), value.into())),
        }
    }

    /// Remove the first pair with `key`, returning its value.
    pub fn remove(&mut self, key: &str) -> Option<Text> {
        let i = self.attributes.iter().position(|(k, _)| k == key)?;
        Some(self.attributes.remove(i).1)
    }

    /// Whether `class` is one of the classes.
    pub fn has_class(&self, class: &str) -> bool {
        self.classes.iter().any(|c| c == class)
    }
}
