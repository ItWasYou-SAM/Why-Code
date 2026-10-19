; Python references: calls and attribute access.
;
; Deliberately shallow: we capture names written literally in code and let
; the extractor decide whether each one is provably resolvable. No type
; inference and no dynamic dispatch.

(call
  function: (identifier) @reference.name) @reference.call

(call
  function: (attribute
    object: (_) @reference.object
    attribute: (identifier) @reference.name)) @reference.attribute_call

(attribute
  object: (_) @reference.object
  attribute: (identifier) @reference.name) @reference.attribute
