-- The metadata's function adds a key; a stateful filter after it counts
-- its calls as they come: each Str gets its number (the added key's
-- among them), the metadata the count when its function is called, and
-- the document a last paragraph with the count at the end.
local n = 0
return {
  {
    Meta = function(m)
      m.added = pandoc.MetaInlines{pandoc.Str("new")}
      return m
    end,
  },
  {
    Str = function(s)
      n = n + 1
      return pandoc.Str(s.text .. n)
    end,
    Meta = function(m)
      n = n + 1
      m.calls = pandoc.MetaString(tostring(n))
      return m
    end,
    Pandoc = function(doc)
      n = n + 1
      doc.blocks:insert(pandoc.Para("calls: " .. n))
      return doc
    end,
  },
}
