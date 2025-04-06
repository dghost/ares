Notes:

Only executables: 
  /.farsight7/sound.bin: SOUND_PUZZLE
  /Security/Cybersecurity/decrypt.sh: DECRYPT

These are the only two executable types listed in the source code

Filenames with whitespace in them (can't interact with):
  '/Legislation/Security/Counter-Intelligence/Internal_Affairs/Astralis. N.file'
  '/Legislation/Security/Counter-Intelligence/Internal_Affairs/Dubois.V.file '
  '/Legislation/Security/Counter-Intelligence/Internal_Affairs/Ibrahim.O.file '
  '/UESC_Departments/Human Resources_&_Personnel.file'




Both ssh and decrypt call to server side API endpoints for validation:
ssh => uesc.io/api/ssh
- Actual commands appear to go through /api/ssh/cli
decrypt => /api/decrypt





"Breadcrumb trails suggest alternate routes."
- Breadcrumbs in schema are somewhat unreliable
- Could have just been a means to find .farsight7 folder. 
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