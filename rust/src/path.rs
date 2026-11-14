//! Where a node is: the path of a filter's error, found once the walk has
//! stopped (so that the walk itself keeps no path). The node is known by its
//! address, which stays where it was: a node in a list is in the list's
//! buffer, which only that list's own walk moves, and it has stopped.

use std::any::TypeId;
use std::collections::BTreeMap;

use crate::Text;

/// What a path is looked for: a node, or a list, by its address and type.
#[derive(Clone, Copy, PartialEq, Eq)]
pub(crate) struct Target {
    addr: usize,
    ty: TypeId,
}

impl Target {
    pub(crate) fn node<T: 'static>(x: &T) -> Self {
        Target {
            addr: x as *const T as usize,
            ty: TypeId::of::<T>(),
        }
    }

    /// A list, by its elements (an empty one has none: no target).
    pub(crate) fn list<T: 'static>(xs: &[T]) -> Option<Self> {
        (!xs.is_empty()).then(|| Target {
            addr: xs.as_ptr() as usize,
            ty: TypeId::of::<[T]>(),
        })
    }
}

enum Step {
    Field(&'static str),
    Index(usize),
    Key(Text),
}

/// The steps to the target, as they are found: what the generated
/// `Locate` impls go through.
pub(crate) struct Path {
    target: Target,
    steps: Vec<Step>,
}

impl Path {
    /// The path to `target` in `root`, as the other languages write one
    /// (`blocks[0].content[1]`, `meta.title`, `meta['a key']`), if it is there.
    pub(crate) fn find<T: Locate + 'static>(root: &T, target: Target) -> Option<String> {
        let mut p = Path {
            target,
            steps: Vec::new(),
        };
        (Target::node(root) == target || root.locate(&mut p)).then(|| p.text())
    }

    fn text(&self) -> String {
        let mut out = String::new();
        for step in &self.steps {
            match step {
                Step::Index(i) => out += &format!("[{i}]"),
                Step::Field(name) => {
                    if !out.is_empty() {
                        out.push('.');
                    }
                    out += name;
                }
                Step::Key(k) => {
                    let plain = k
                        .chars()
                        .next()
                        .is_some_and(|c| c.is_alphabetic() || c == '_')
                        && k.chars().all(|c| c.is_alphanumeric() || c == '_');
                    if plain {
                        out.push('.');
                        out += k;
                    } else if k.contains('\'') {
                        out += &format!("[{:?}]", k.as_str());
                    } else {
                        out += &format!("['{k}']"); // (as Python writes it)
                    }
                }
            }
        }
        out
    }

    /// `found` with `step` taken: whether the target is there (else the
    /// step is taken back).
    fn step(&mut self, step: Step, found: impl FnOnce(&mut Self) -> bool) -> bool {
        self.steps.push(step);
        let yes = found(self);
        if !yes {
            self.steps.pop();
        }
        yes
    }

    pub(crate) fn field(
        &mut self,
        name: &'static str,
        found: impl FnOnce(&mut Self) -> bool,
    ) -> bool {
        self.step(Step::Field(name), found)
    }

    pub(crate) fn index(&mut self, i: usize, found: impl FnOnce(&mut Self) -> bool) -> bool {
        self.step(Step::Index(i), found)
    }

    pub(crate) fn list<T: 'static>(
        &mut self,
        xs: &[T],
        each: impl Fn(&mut Self, &T) -> bool,
    ) -> bool {
        if Target::list(xs) == Some(self.target) {
            return true;
        }
        xs.iter().enumerate().any(|(i, x)| {
            let target = self.target;
            self.index(i, |p| Target::node(x) == target || each(p, x))
        })
    }

    pub(crate) fn map<T: 'static>(
        &mut self,
        m: &BTreeMap<Text, T>,
        each: impl Fn(&mut Self, &T) -> bool,
    ) -> bool {
        m.iter().any(|(k, x)| {
            let target = self.target;
            self.step(Step::Key(k.clone()), |p| {
                Target::node(x) == target || each(p, x)
            })
        })
    }
}

/// A type whose values hold nodes: where in it the target is (generated).
pub(crate) trait Locate {
    fn locate(&self, p: &mut Path) -> bool;
}

/// A list given whole (a fragment).
impl<T: Locate + 'static> Locate for Vec<T> {
    fn locate(&self, p: &mut Path) -> bool {
        p.list(self, |p, x| x.locate(p))
    }
}
