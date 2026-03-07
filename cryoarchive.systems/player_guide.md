# cryoarchive.systems — Notes

Note: this is *all* speculative, based on the client side source code of the website. This event is clearly intended to be a fairly major community event with multiple milestones. Any of it could change at any time.

Extra Note: ARG spoilers, yo. Don't read if you want to experience it blind or figure it out for yourself.

## Progression

Landing page is a CCTV grid with 9 cameras. Each camera tile shows its status and is the entry point for everything.

**Camera capture** (all 9 cameras):

1. **Unstable** → starting state. Grayscale feed, progress bar, countdown.
2. **Stable** → clear feed. Click to fullscreen (plays detail video). **Shift+S** to capture.
3. **Captured** → server validates. Camera 06 and Camera 09 stop here (no rooms).

**Room challenges** (7 cameras have rooms, unlocked by capture):

4. **Unlocked** → room accessible. Tile shows `[D{n}] Run_Diag`. Navigate via click, keys **1-7**, or navbar.
5. **Completed** → challenge finished.

Cargo's "room" is the CCTV grid itself — no separate challenge, server sets completion on capture. Control's room is double-gated: capture unlocks the page, but the challenge requires `memoryUnlocked` (an external server flag, presumably set by another part of the ARG). The remaining 5 rooms have standalone challenges.

Session lasts 7 days, then resets.

## The Cameras

Per-camera stabilization is server-controlled. 7 cameras unlock rooms, 2 don't (Camera 06, Camera 09).

Each camera has three video assets:
- **Unstable** — grayscale glitch loop (plays while unstabilized)
- **Stable** — clear still frame (plays when stabilized)
- **Detail** — 15-sec color clip, blue/teal lighting (plays on capture)

### Camera Feeds

- **Cargo** — Storage bay. Tall pillar, red warning display.
- **Index** — Dim control room. Equipment racks, blue indicator dots.
- **Revival** — Cryo processing line. "LOAD" stenciled on wall. Hazard striping.
- **Biostock** — Frozen storage. Yellow-green canisters, checkerboard hazard markings.
- **Steerage** — Body-sized green objects piled in disarray. Curved hull.
- **Preservation** — Pipes, pressure vessels, blue coolant cylinders.
- **Control** — Rows of blue cryo pods. Red/green/yellow status indicators.
- **Camera 06** — Frozen corridor. "UESC" on wall, "BIOSTOCK" on ceiling.
- **Camera 09** — Vertical shaft, wall of grid cells. Only feed with temporal change.

## The Rooms

Keys **1-7**, navbar, or click camera tile to navigate. **X** to close.

### [1] Cargo (Home)
Main CCTV grid. Explicitly excluded from challenge mechanics. Server sets completion state.

### [2] Index — Dual Authentication
Two sequential gates:
1. **DAC file upload** — click to upload credential file (establishes session)
2. **Password** — typed as CodeLanguage glyphs, **E** to focus, **Enter** to submit (unlocks the page)

Unlocks archive entries: text, PDFs, images, video, audio.

### [3] Steerage — Fog of War
60x40 grid over hidden image. Click-drag rectangles to reveal. Server returns image fragments per rectangle. Plays video on completion.

### [4] Revival — Diagnostic Quiz
Timed trivia with tiers (one question per tier). Each question has a 5-second reveal countdown before you can answer. Answers typed in CodeLanguage glyphs. Wrong answer or timeout resets all tiers. Plays video on completion.

### [5] Biostock — Direction Sequence
Gaussian splat point cloud scene. Not a spatial maze — it's a sequence puzzle. Choose a direction (arrow keys or clickable buttons), the camera flies toward that door and re-enters from a random one (purely visual). The server validates each move via `POST /api/point-cloud/move`. Find the correct sequence of directions to complete. Path displayed as arrow trail. **R** to reset. Plays video on completion.

### [6] Preservation — Decryptor
Eight timed lock puzzles on a single scrollable page. Each has a serial number. Any timer expiry resets all.

- **Calculation** — solve a math problem from a grid of numbers
- **Cable** — cut named colored wires in the correct order
- **Drawing** — trace a path through a grid of labeled points (adjacency-constrained)
- **Sliders** — drag sliders to the correct positions (count set by server)
- **Pattern** — toggle cells in a grid to match a pattern, then submit
- **Combination** — scroll horizontally to set each number, reversing direction between digits
- **Password** — view an image and type the correct password
- **Hold** — hold anywhere on the page while a video plays, release at the right moment

Plays video on completion.

### [7] Control (Cryo Hub)
No puzzle. Shows "Unauthorized Access" until `memoryUnlocked` fires — then the red filter clears, a video (the "memory") becomes available, and watching it completes the room. A kill counter component exists in the code (progress bar + "X / Y uesc killed" + reconnection countdown) but is not yet wired into the page.

### [8] Example (Hidden)
Eighth navbar slot. Returns error.

## Open Questions

- **Camera 06 and Camera 09** — feeds with no rooms - seem to allow capturing, unclear if necessary or not
- **4 phantom videos** — alternate video UUIDs that 404 - completion videos?
- **Decryptor serial numbers** — each lock has a unique serial - unclear if meaningful
- **memoryUnlocked** — trigger unknown