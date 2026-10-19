; References: calls and attribute access. Deliberately shallow — no type
; inference, no dynamic dispatch. Only names written literally in code.

(call
  function: (identifier) @call_name) @call

(call
  function: (attribute
    attribute: (identifier) @method_name)) @method_call

(attribute
  attribute: (identifier) @attribute_name) @attribute

(identifier) @identifier
