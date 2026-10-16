# Nested specifications

Register the top-level `looprd.yaml` once in the sources file. It can explicitly
import fragments from its own folder or subfolders:

```yaml
apiVersion: looprd/v1
component: example
release: '1.0'
api: {version: v1}
auth: {mode: public}
requires: [spec.includes]
includes:
  - accounts/looprd.yaml
  - inventory/looprd.yaml
```

Each imported file is a partial specification, for example:

```yaml
includes:
  - status/looprd.yaml
handlers:
  - path: /hello
    response: {message: Hello}
```

Imports extend **one component**, sharing its URL prefix, authentication, database,
migration history and lifecycle. They do not register separate servers. The root
must define `apiVersion`, `component`, `release`, `api` and `auth`; only the root
may declare `database` and `realtime`. Models, resources, handlers, WebSocket
subscriptions, jobs, management grants and other component settings may be split
across fragments. Database migrations remain together in the root declaration.

Mappings combine recursively, with disjoint definitions required; lists append
in root-first, depth-first include order. Repeating a scalar definition is an
error even when its value matches. There are no silent overrides. Final combined
validation checks permissions, references, paths and management grants together.
Imported fragments are not independently valid complete component documents.
Editors can use `schemas/looprd-fragment.schema.json` for fragment completion and
`schemas/looprd.schema.json` for the root; runtime validation resolves the tree.
`python -m looprd validate examples/nested/looprd.yaml` validates the entire tree.

Paths resolve relative to the file containing `includes`. Use plain descendant
paths, with no absolute paths, URLs, parent traversal, encoded paths, queries,
fragments or backslashes. Local symlinks cannot escape the containing directory.
Imports remain on the same HTTPS origin or GitHub repository; GitHub trees are
read at one resolved immutable commit. Generic HTTPS files are fetched afresh,
so their publisher must keep multi-file updates consistent. No folder scanning,
glob matching or remote cross-repository imports are performed.

Limits: 32 direct imports per file, 64 files total including the root, eight
import levels, and 1 MiB aggregate UTF-8 document content. A repeated file,
cycle, conflict or invalid/unreachable child rejects the candidate generation.
The last working generation remains active, and failures appear through existing
management diagnostics. Every watcher poll reads the import tree, so a child-only
change reloads the component. Checkpoints store the flattened validated result,
allowing restart while the source repository is unavailable.

Explicitly removing an include removes the declarations it contributed after
successful validation. It never deletes persisted application data. Removing
models or changing their schema still obeys existing migration/compatibility rules
and may require retaining model declarations while removing exposed routes.

Legacy specifications without `includes` keep their existing behavior. Older
Looprd images reject `includes`; deploy a compatible image before changing an
application specification. To roll back, flatten the documents into one file.
See the complete [nested example](../examples/nested/looprd.yaml).
