; Python definitions.
;
; Captures follow Tree-sitter's code-navigation convention:
;   @definition.function / @definition.class / @definition.constant
;   @name, @parameters, @body, @value
;
; A decorated definition matches through its inner node; the extractor looks
; at the parent to recover the decorators and the full declaration span.

(function_definition
  name: (identifier) @name
  parameters: (parameters) @parameters
  body: (block) @body) @definition.function

(class_definition
  name: (identifier) @name
  body: (block) @body) @definition.class

(assignment
  left: (identifier) @name
  right: (_) @value) @definition.constant

; Explicit PEP 695 alias: ``type UserId = int``.
(type_alias_statement
  left: (type (identifier) @name)
  right: (_) @value) @definition.type
