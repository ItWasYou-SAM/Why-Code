; Python imports: absolute and relative, plain and from, aliased and wildcard.
;
; Every statement and every name it binds is captured explicitly. The
; extractor groups captures by statement node, so ``from x import a, b`` is
; one import with two names.

(import_statement
  name: (dotted_name) @import.name) @import.statement

(import_statement
  name: (aliased_import
    name: (dotted_name) @import.name
    alias: (identifier) @import.alias)) @import.statement

(import_from_statement
  module_name: (dotted_name) @import.module
  name: (dotted_name) @import.name) @import.from_statement

(import_from_statement
  module_name: (dotted_name) @import.module
  name: (aliased_import
    name: (dotted_name) @import.name
    alias: (identifier) @import.alias)) @import.from_statement

(import_from_statement
  module_name: (relative_import) @import.relative_module
  name: (dotted_name) @import.name) @import.relative_from_statement

(import_from_statement
  module_name: (relative_import) @import.relative_module
  name: (aliased_import
    name: (dotted_name) @import.name
    alias: (identifier) @import.alias)) @import.relative_from_statement

(import_from_statement
  module_name: (_) @import.module
  (wildcard_import) @import.wildcard) @import.from_statement
