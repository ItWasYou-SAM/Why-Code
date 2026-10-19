; Imports: absolute and relative, plain and from-import, aliased and wildcard.

(import_statement
  name: (dotted_name) @import_module) @import_stmt

(import_statement
  name: (aliased_import
    name: (dotted_name) @import_module)) @import_stmt

(import_from_statement
  module_name: (dotted_name) @from_module) @from_stmt

(import_from_statement
  module_name: (relative_import) @from_relative) @from_stmt
