# Version v0.1.3 Changes

## Summary
Production-safe slicing worker API with job isolation, proper support typing, and clean response separation.

---

## New Features

### 1. Job Isolation
- Every request gets a unique `job_id` and isolated folder at `temp/jobs/{job_id}/`
- Files per job:
  - `input.stl` - uploaded model
  - `output.3mf` - OrcaSlicer output
  - `result.json` - Orca result metadata
  - `process_override.json` - temporary process config
- Concurrent requests cannot overwrite each other

### 2. Fixed Support Override Typing
- Uses only `enable_support` key (Orca Bambu Lab standard)
- Writes as **string values**: `"0"` or `"1"`
- Removed: `generate_support`, `support`, `support_enable`, `support_generate`
- Removed entire `try_alternate_types()` function
- `layer_height` normalized: strips "mm" suffix, outputs as string (e.g., `"0.2"`)

### 3. Better Auto-Support Behavior
- Auto-retry only when supports not explicitly disabled
- Honest reporting of `warning_still_present_after_retry`
- Clear `support_detection` object with:
  - `supports_explicitly_requested`
  - `requested_supports`
  - `floating_regions_detected`
  - `auto_retry_attempted`
  - `auto_retry_success`
  - `support_override_applied`
  - `support_key_used` (always `"enable_support"`)
  - `support_value_written`
  - `warning_still_present_after_retry`
  - `auto_retry_skipped_reason` (when applicable)

### 4. Production vs Debug Response Separation
- Normal response:
  ```json
  {
    "success": true,
    "metadata": {...},
    "warnings": [...],
    "support_detection": {...}
  }
  ```
- Debug response (only when `current_app.debug` or `DEBUG=true` env):
  - Adds `debug` object with:
    - `job_id`, `job_folder`
    - `stdout`, `stderr` (when `debug=true` param)
    - `process_used`, `applied_overrides`
    - `orca_return_code`
    - `output_3mf_base64` (only if < 5MB)
- `include_3mf` is debug/admin-only
- Never exposes internal paths in production

### 5. Sanitized Orca Warnings
- Categorizes warnings:
  - `acceleration_capped`
  - `floating_regions`
  - `thumbnail_opengl` (Wayland/GLEW/OpenGL/XDG - non-fatal)
  - `no_filament_colors`
  - `invalid_json_type`
- Treats OpenGL/thumbnail errors as non-fatal if `return_code == 0`
- Clean `warnings` array in response

### 6. Safer Profile Selection
- New API params (preferred):
  - `quality=standard_020`, `quality=fine_012`, etc.
  - `material=pla`, `material=petg`, etc.
- Legacy `process`/`filament` params still supported with path traversal validation
- Returns 400 for unknown material/quality

### 7. Job Cleanup
- `cleanup_old_jobs(max_age_hours=24)` runs at most once per hour
- Only cleans job folders older than 24 hours
- Never deletes active job folder

---

## API Changes

### Request Parameters (POST /)
- **New Parameters**:
  - `quality`: Profile ID (e.g., `standard_020`)
  - `material`: Material type (e.g., `pla`, `petg`)
- **Legacy Parameters** (still supported):
  - `process`: Process filename
  - `filament`: Filament filename
- **Settings**:
  - `supports`: Boolean - Enable/disable supports
  - `layer_height`: String/number - Override layer height

### Response Format (POST /)
```json
{
  "success": true,
  "metadata": {
    "used_mm": float,
    "used_cm3": float,
    "max_z": float,
    "filament_type": string,
    "default_print_profile": string,
    "model_time": float (minutes),
    "total_time": float (minutes),
    "first_layer_time": float (minutes)
  },
  "warnings": [string],
  "support_detection": {
    "supports_explicitly_requested": boolean,
    "requested_supports": boolean | null,
    "floating_regions_detected": boolean,
    "auto_retry_attempted": boolean,
    "auto_retry_success": boolean,
    "support_override_applied": boolean,
    "support_key_used": "enable_support",
    "support_value_written": "0" | "1",
    "warning_still_present_after_retry": boolean,
    "auto_retry_skipped_reason": string (optional)
  }
}
```

### Debug Response (when debug mode enabled)
```json
{
  "success": true,
  "metadata": {...},
  "warnings": [...],
  "support_detection": {...},
  "debug": {
    "job_id": string,
    "job_folder": string,
    "process_used": string,
    "applied_overrides": object,
    "orca_return_code": number,
    "stdout": string,
    "stderr": string
  }
}
```

---

## Error Codes
- `orca_missing`: OrcaSlicer binary not found
- `missing_process`: Process config not found
- `missing_filament`: Filament config not found
- `process_override_failed`: Failed to create temp process
- `timeout`: OrcaSlicer timed out
- `orca_error`: Orca reported error
- `missing_output`: Output 3MF not created
- `missing_file`: No stlFile or gcsLink provided
- `invalid_file`: Uploaded file not an STL
- `bad_request`: Settings must be JSON object
- `bad_settings`: Unsupported settings keys
- `invalid_layer_height`: layer_height must be valid number
- `unknown_quality`: Unknown quality ID
- `unknown_material`: Unknown material type
- `invalid_process`: Process filename not allowed
- `invalid_filament`: Filament filename not allowed
- `gcs_download_failed`: Failed to download from GCS

---

## Environment Variables
- `ORCASLICER_BIN`: Path to OrcaSlicer binary (default: 'orcaslicer')
- `ORCA_TIMEOUT`: Timeout in seconds (default: 300)
- `DEBUG`: Set to 'true' to enable debug mode

---

## Backward Compatibility
- All previous versions remain accessible at their URL prefixes
- Default API version changed to `/v0.1.3`
- Legacy `process`/`filament` params still work with validation

---

## File Structure
```
versions/v0_1_3/
├── routes.py          # Main route handlers
├── changes.md         # This file
└── __pycache__/       # Python cache
```
