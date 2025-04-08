## Notes

### Filesystem
Only executables:

| File                                  | Type            |
|---                                    |---              |
| `/.farsight7/sound.bin`               | `SOUND_PUZZLE`  |
| `/Security/Cybersecurity/decrypt.sh`  | `DECRYPT`       |
 
SOUND_PUZZLE and DECRYPT are the only two executable types mentioned in source code, so seems unlikely there are additional hidden binaries at this time.

Filenames with whitespace in them that can't be interacted w/ through terminal:
```
'/Legislation/Security/Counter-Intelligence/Internal_Affairs/Astralis. N.file'
'/Legislation/Security/Counter-Intelligence/Internal_Affairs/Dubois.V.file '
'/Legislation/Security/Counter-Intelligence/Internal_Affairs/Ibrahim.O.file '
'/UESC_Departments/Human Resources_&_Personnel.file'
```
Note: client CLI doesn't parse these because it splits on whitespace. So either this is deliberate (for effect) or a bug.

### SSH
Both ssh and decrypt call to server side API endpoints for validation:
* `ssh` => `uesc.io/api/ssh`
  - Actual commands appear to go through `/api/ssh/cli`
* `decrypt` => `uesc.io/api/decrypt`

Can't easily reverse engineer these from client source.

### Breadcrumbs?
"Breadcrumb trails suggest alternate routes."
- Breadcrumbs in schema are somewhat unreliable
- Could have just been an alternate means to find .farsight7 folder. 
    - 'ls' explicitly hides files/folders with names beginning with '.'
    ```
        d = [
          ...M.map(e => e.name.startsWith('.') ? null : e.name).filter(Boolean),
          ...u.map(e => e.startsWith('.') ? null : e).filter(Boolean)
        ].filter(Boolean).sort((e, t) => e.localeCompare(t));
    ```
- At least two folders have breadcrumbs whose names don't match
- Traversal doesn't appear to actually involve breadcrumbs at all. instead uses names, id's, and parent ids?
- there are theoretical cycles in the graph. need to prove this.

### API Endpoints

| Endpoint                | Desc                                    |
|---                      |---                                      |
| `/api/media`            | Static content like images              |
| `/api/ssh`              | SSH command                             |
| `/api/ssh/cli`          | SSH command trampoline                  |
| `/api/decrypt`          | Decrypt command                         |
| `/api/terminal-config`  | Background scroll while in the terminal | 
| `/api/folders`          | All files and folders in the terminal   |
 