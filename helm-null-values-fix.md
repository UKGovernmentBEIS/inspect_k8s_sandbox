# Helm Null Values Fix - Session Notes

## Problem Statement

The test `test/k8s_sandbox/test_volume.py` was failing because PersistentVolumeClaims (PVCs) were not being created for volumes defined with null values in the values YAML:

```yaml
volumes:
  shared:  # null value
```

## Root Cause

**Helm filters out null values from maps during template processing.** This is documented behavior in [helm/helm#11617](https://github.com/helm/helm/issues/11617).

When you define:
```yaml
volumes:
  simple-volume-1:
  simple-volume-2:
  custom-volume:
    spec:
      storageClassName: nfs-client
```

Helm's value merging logic treats null as "delete this key". Only `custom-volume` (with non-null value) survives to reach the template. The PVC template's `{{- range $name, $volume := .Values.volumes }}` loop never sees `simple-volume-1` or `simple-volume-2`.

## Initial Wrong Approach

I initially tried to fix this in the Helm template itself by:
1. Collecting volume names from service references (e.g., `"shared:/mount"`)
2. Creating PVCs based on those references

**Why this was wrong:** This changed the semantics. The original design creates PVCs for ALL volumes declared in `volumes:`, not just ones referenced by services.

## Correct Solution

**Preprocess the values YAML before passing it to Helm**, converting null volumes to empty dicts `{}`:

```python
# Before: volumes: {shared: null}
# After:  volumes: {shared: {}}
```

### Implementation

Created `ProcessedValuesSource` class in `src/k8s_sandbox/_helm.py`:
- Loads the original values file
- Converts null volume values to `{}`
- Writes preprocessed values to a temp file
- Passes temp file to Helm
- Cleans up temp file after use

Updated `_create_values_source()` in `src/k8s_sandbox/_sandbox_environment.py` to use `ProcessedValuesSource` for all non-compose values files.

Updated `_run_helm_template()` in `test/k8s_sandbox/helm_chart/test_helm_chart.py` to use `ProcessedValuesSource` so tests preprocess values the same way as production code.

## Why This Works

- Helm doesn't filter out `{}` (empty dict) - it's a valid non-null value
- The template condition `{{- if and $volume $volume.spec }}` evaluates:
  - `$volume` = truthy (it's `{}`)
  - `$volume.spec` = falsy (doesn't exist)
  - Combined: falsy → uses default spec
- Original semantics preserved: null/empty = "use defaults"

## Key Learning

**You cannot fix Helm's null-filtering behavior in templates.** The filtering happens during value loading/merging, before templates execute. The fix must happen in the code that prepares values before invoking Helm.

## Files Modified

1. `src/k8s_sandbox/_helm.py` - Added `ProcessedValuesSource` class
2. `src/k8s_sandbox/_sandbox_environment.py` - Use `ProcessedValuesSource` for values files
3. `test/k8s_sandbox/helm_chart/test_helm_chart.py` - Use `ProcessedValuesSource` in test helper

## Test Results

✅ `test/k8s_sandbox/test_volume.py::test_volumes` - PASSED
✅ `test/k8s_sandbox/helm_chart/test_helm_chart.py::test_volumes` - PASSED
✅ All 18 helm chart tests - PASSED

## Alternative Approaches Considered

1. **Collect volumes from service references only** - Rejected because it changes semantics
2. **Document that users must write `volumes: {shared: {}}` instead of `volumes: {shared:}`** - Rejected because it's user-hostile and the test file explicitly uses null values
3. **Fork Helm and patch the null-filtering behavior** - Rejected as unrealistic

## Future Considerations

- This preprocessing happens for every sandbox environment creation
- The temp file I/O overhead is negligible compared to Helm install time
- If Helm ever changes null-handling behavior (unlikely), we can adjust or remove `ProcessedValuesSource`
- Consider whether other null values in the Helm chart might need similar preprocessing
