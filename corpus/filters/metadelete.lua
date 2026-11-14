-- A function for metadata values that returns nothing for a map's value
-- (a field of the metadata): an error in every language, saying to delete
-- the key from the map instead (in a list, nothing deletes the value:
-- scenario metavalues). pandoc's Lua has no such function: here the Meta
-- function fails where panir's languages do.
-- fails
-- stateless: the same with traverse = "bottomup"
return {{
  Meta = function(m)
    if type(m.draft) == "boolean" then error("a map's value is replaced by one") end
  end,
}}
