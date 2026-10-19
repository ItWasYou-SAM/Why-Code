; Definitions: classes, functions, methods, module-level constants.
; Decorated definitions are captured through the inner node so a single
; extraction path handles both forms.

(class_definition
  name: (identifier) @class_name) @class_def

(function_definition
  name: (identifier) @function_name) @function_def

(decorated_definition
  definition: (class_definition
    name: (identifier) @class_name) @class_def)

(decorated_definition
  definition: (function_definition
    name: (identifier) @function_name) @function_def)

(module
  (expression_statement
    (assignment
      left: (identifier) @constant_name) @constant_def))
