-- Top-down, what a function returns is not given to a function (the one
-- that returned it or any other), nor, if a list, are its elements; their
-- children are walked. A list function's result is the list whose elements
-- are walked. Each call is noted; a second filter adds a last paragraph
-- listing them.
local seen = {}
local function note(s) seen[#seen + 1] = s end
return {
  {
    traverse = "topdown",
    -- spliced: an emphasis directly in it stays one, and a Str directly in
    -- it is not noted
    Emph = function(e) note("Emph") return e.content end,
    Div = function(d) note("Div") return d.content end,
    Strong = function(s)
      local text = pandoc.utils.stringify(s)
      note("Strong:" .. text)
      if text == "other" then  -- a node of another type
        return pandoc.Underline{pandoc.Strong{pandoc.Str("in")}, pandoc.Emph{pandoc.Str("e")}}
      elseif text == "two" then  -- two nodes
        return {pandoc.Strong{pandoc.Str("s")}, pandoc.Underline{pandoc.Strong{pandoc.Str("u")}}}
      end
    end,
    Underline = function() note("Underline") end,
    Str = function(s) note("Str:" .. s.text) end,
    Inlines = function(xs)
      if #xs > 0 and xs[1].text == "list" then
        note("Inlines")
        return {pandoc.Emph{pandoc.Str("new")}, pandoc.Strong{pandoc.Emph{pandoc.Str("g")}}}
      end
    end,
  },
  {
    Pandoc = function(doc)
      doc.blocks:insert(pandoc.Para(table.concat(seen, " ")))
      return doc
    end,
  },
}
