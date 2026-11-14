-- Top-down, the document's function returns a new document, and that is
-- what is walked: a header of the old one's title and a paragraph, their
-- Strs upper-cased; the old document's blocks are gone.
return {{
  traverse = "topdown",
  Pandoc = function(doc)
    return pandoc.Pandoc({pandoc.Header(1, doc.meta.title), pandoc.Para("made anew")}, doc.meta)
  end,
  Str = function(s) return pandoc.Str(s.text:upper()) end,
}}
