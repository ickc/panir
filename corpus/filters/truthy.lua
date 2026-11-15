-- Lua's truth of metadata values (`if meta.x then`, `meta.x or d`): false
-- for a missing field and for `false`, true for anything else, an empty
-- string, list or map and the text "no" included. A paragraph says each.
-- stateless: the same with traverse = "bottomup"
local keys = {"yes", "no", "text", "empty", "null", "noword", "zero", "list", "map", "block", "missing"}
return {{
  Pandoc = function(doc)
    local out = {}
    for _, k in ipairs(keys) do
      out[#out + 1] = k .. "=" .. (doc.meta[k] and "true" or "false")
    end
    local stringify = pandoc.utils.stringify
    out[#out + 1] = "no-or=" .. stringify(doc.meta.no or "d")
    out[#out + 1] = "text-or=" .. stringify(doc.meta.text or "d")
    doc.blocks:insert(pandoc.Para(table.concat(out, " ")))
    return doc
  end,
}}
