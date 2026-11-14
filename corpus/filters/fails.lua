-- A function that raises an error stops the conversion: the second
-- paragraph's, after the first was changed. pandoc writes no document.
-- fails
local n = 0
return {{
  Para = function(p)
    n = n + 1
    if n == 2 then error("the second paragraph") end
    p.content:insert(pandoc.Str("!"))
    return p
  end,
}}
