-- pandoc.utils.to_roman_numeral: 1 to 3999 in Roman numerals, 0 nothing,
-- anything else (negative, 4000 or more) "?". A paragraph of each.
-- stateless: the same with traverse = "bottomup"
local numbers = {0, 1, 4, 9, 14, 40, 90, 400, 1994, 2026, 3999, 4000, 1000000, -1}
return {{
  Pandoc = function(doc)
    local out = {}
    for _, n in ipairs(numbers) do
      out[#out + 1] = n .. "=" .. pandoc.utils.to_roman_numeral(n)
    end
    doc.blocks:insert(pandoc.Para(table.concat(out, " ")))
    return doc
  end,
}}
