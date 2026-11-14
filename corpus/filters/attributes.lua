-- Attributes by key, a key perhaps in two pairs: the first pair with a
-- key is read, set (in its place) and removed; a new key goes at the end.
-- A span with `k` gets `first`, the first `k`'s value, and that `k` an
-- exclamation mark; its first `drop` goes; a span of class `mark` gets
-- class `seen` too.
-- stateless: the same with traverse = "bottomup"
return {{
  Span = function(s)
    local a = s.attributes
    if a.k then
      a.first = a.k
      a.k = a.k .. "!"
    end
    a.drop = nil
    if s.classes:includes("mark") then s.classes:insert("seen") end
    return s
  end,
}}
