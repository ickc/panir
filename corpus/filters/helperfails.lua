-- A helper reading an option fails, and the filter with it: an option
-- under `indent`, which is `true` (Lua fails indexing a boolean, as a
-- filter reading a nested option does). pandoc writes no document.
-- fails
local function size(config)
  return config.size
end
return {{
  Meta = function(meta)
    meta.size = size(meta.indent)
    return meta
  end,
}}
