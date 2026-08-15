# ACadSharp portable candidate backend

This directory contains a source-only, read-only DWG candidate reader for Windows computers that do not have ZWCAD installed.
It pins [ACadSharp 3.6.51](https://www.nuget.org/packages/ACadSharp/3.6.51), verifies the NuGet package SHA-256, and builds outside the repository with the Windows .NET Framework compiler.

ACadSharp originates from the upstream [DomCR/ACadSharp](https://github.com/DomCR/ACadSharp) project and is distributed under the [MIT License](https://github.com/DomCR/ACadSharp/blob/master/LICENSE). The upstream library supports both reading and writing CAD formats; this repository is independent of that project and deliberately exposes only a read-only analysis-copy workflow. It does not vendor ACadSharp source or binaries.

## Run

```powershell
.\scripts\运行ACadSharp只读候选提取.ps1 `
  -InputPath 'D:\Project\drawing.dwg' `
  -WorkRoot 'D:\CadWork\acadsharp-candidate'
```

The runner hashes the original, copies it to a non-synchronized work root, verifies the copy, opens only that copy, and verifies the original hash again. Build artifacts and the NuGet cache default to `%LOCALAPPDATA%\CadReadingToolkit\Portable` and are never written into this repository.
Each reader process has a 300-second default timeout (`-TimeoutSeconds` can be set explicitly); a timeout stops only that spawned reader and records exit code 124.

Outputs are `analysis\acadsharp-portable-evidence.json` and `analysis\acadsharp-portable-run.json`. The evidence JSON contains recursive candidate records for TEXT, MTEXT, ATTRIB, INSERT, LINE, LWPOLYLINE, POINT, CIRCLE and ARC, with handles, root space, block path and candidate world coordinates.
Attribute definitions are emitted as `ATTDEF` with `definition_template_not_placed_value=true`; they are not mislabeled as placed TEXT/ATTRIB evidence. MINSERT records include row/column counts and spacing, but their occurrences are not expanded.

## Safety boundary

- This is a comparison candidate, not a replacement for the validated ZWCAD V5/V6/V7/V10/V13/V18 backend.
- Parser notifications, unsupported reachable entities, non-uniform insertion scale, MINSERT, proxy objects, xrefs, dynamic-block state and transform failures remain explicit unresolved evidence.
- Layout viewport clipping, frozen layers and final visibility are not implemented.
- ATTRIB coordinates are parser candidates; real regression has already found field differences from ZWCAD.
- `formal_backend_equivalent` and `absence_proven` are always `false`. A negative search result cannot prove that an object is absent.
- The reader contains no DWG writer path and never opens the original input file.
- Concurrent runs must use distinct `-BuildRoot` directories; the source-only build step is not serialized across processes.

For a field-level comparison against existing ZWCAD V5/V6/V10/V13 JSON exports, run
`scripts\compare_acadsharp_portable_with_zwcad.py`. Its result always remains
`candidate_field_comparison_unresolved`; exact matches for selected fields do not establish backend equivalence.

Validate a result with:

```powershell
python .\scripts\validate_acadsharp_portable_output.py `
  'D:\CadWork\acadsharp-candidate\analysis\acadsharp-portable-evidence.json'
```
