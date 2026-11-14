"""
pandoc's document AST, generated from pandoc-types: types, pandoc's JSON,
walking and filters.

```julia
using Panir
doc = Panir.parse(json)                 # checked
upper(s::Str, ctx) = Str(uppercase(s.text))    # a method per node type
walk!(upper, doc)
Panir.serialize(doc)
```
"""
module Panir

import JSON
using PrecompileTools: @setup_workload, @compile_workload

"Any node of pandoc's AST: a constructor (`Str`, `Para`, ...) or a product (`Attr`, ...)."
abstract type Node end

# A node's inner constructor takes this, then its fields as they are (from
# JSON, or a constructor that made them its own).
struct _Fields end
const _FIELDS = _Fields()

include("generated.jl")
include("core.jl")
include("onepass.jl")
include("walk.jl")
include("meta.jl")

include("precompile.jl")

export Node, ASTDecodeError, ASTTypeError, ASTDepthError, Context, Conversion, walk!, skip_children!, check_unique, run_filter, stringify, inlines, blocks, truthy, lookup

end
