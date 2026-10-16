# fantavision-converter

Converts **Fantavision** animations (Broderbund, Amiga, 1988) into **animated SVG** files that play in any web browser (Firefox, Chrome, Safari, Edge).

Fantavision is a keyframe-based vector animation program: a movie file only stores polygons at keyframes, and the player computes the in-between frames by interpolation ("tweening"). The generated SVG does exactly the same thing using SMIL animations (`<animate attributeName="points">`), with no JavaScript. The files stay tiny (1 to 60 KB) and sharp at any size.

## Usage

Python 3, no dependencies.

To convert every movie in the `movies/` folder at once:

```bash
./convert-movies.sh              # writes the SVG files to svg-output/
./convert-movies.sh --step 0.04  # options are passed through to fant2svg.py
```

Then open the files in `svg-output/` in a web browser (some image viewers, such as Ubuntu's default one, do not play SVG animations). The SVG files are not versioned: they are regenerated from the sources.

For a single movie:

```bash
python3 fant2svg.py MOVIE [output.svg] [--step 0.05] [--arrival] [--debug] [--no-sound]
```

| Option | Effect |
|---|---|
| `--step S` | duration of one in-between step in seconds (default 0.05 s). The real speed was set by the player (keys 1–0, or `SPEED` in a script), not by the movie file |
| `--arrival` | a keyframe's in-between count applies to the transition from the previous keyframe (default: to the next one) |
| `--debug` | shows `$7000`-type objects, normally hidden, as dashed pink outlines |
| `--no-sound` | leaves the sounds out (no script in the SVG) |

The movies are found in the `Movies` folder of the Fantavision disk. To extract them from an `.adf` image, any Amiga floppy (OFS) reader will do.

## What is supported

- Every object type of the player: filled polygons (optionally with a border in a second colour), lines, outlines, round and square dots, bitmaps, bitmaps stamped on each point, and text. Morphing works even when the number of points changes from one keyframe to the next (resampling).
- "Comet" trails: objects flagged to leave a trace are painted step by step into the background.
- Colours interpolated by palette index, step by step, with a palette per keyframe, and the background colour chosen in the header.
- ILBM bitmaps (background and objects), converted to embedded PNG, cropped to their real size, with colour 0 transparent when the object asks for a mask.
- Text (`CSTR`), word-wrapped in its box, with a substitute browser font.
- Repeated blocks of keyframes.
- Objects appearing or disappearing between keyframes.
- Looping: if the last keyframe still contains objects, it morphs back into the first one.
- Amiga pixel aspect ratio (NTSC 320×200 displayed as 4:3).
- Sound: the 8SVX samples are embedded in the SVG and played with a small Web Audio script, on two channels like the original, with volume and pitch changes interpolated between keyframes.

Not supported: Amiga bitmap fonts, and colour changes of bitmaps when the palette changes (bitmaps keep the palette of the first keyframe).

### Sound

Browsers do not allow a page to play sound by itself, so movies with sound get a small **♪ off** button in the top-right corner. Clicking it restarts the movie from the beginning with sound; clicking again mutes it. The script only runs when the SVG is opened directly in a browser (or embedded with `<object>`/`<iframe>`), not through an `<img>` tag. Several of the movies have sound, among them *Jaws*, *Dinosaurs*, *Aliens* and *CarCrash!*.

## The FANT format

A standard IFF file. All numbers are big-endian 16-bit words (signed for coordinates). Most of what follows was confirmed by disassembling `FantaPlayer` (the Amiga Fantavision Player, 3/9/88), included in this repository.

```
FORM FANT
  FHDR (92 bytes)   global header
  FORM ILBM ...     background (if any), then bitmaps
  FORM 8SVX + SEFX  sounds and their triggers
  FRAM (88 bytes)   start of a keyframe
    POLY ...        keyframe objects, in drawing order (back -> front)
    CSTR ...        text then font name, for each text object
  FRAM ...
```

### FHDR

| Offset | Content |
|---|---|
| 0 | `$0040` |
| 2 | `$0010` |
| 4 | depth (5 = 32 colours) |
| 6, 8 | width, height (320×200, 352×276…) |
| 10 | background colour: index of the colour the screen is cleared with (0, 2, 15 in the samples) |
| 12 | palette: 32 `$0RGB` words |
| 76 | number of keyframes |
| 78 | number of sounds |
| 80 | number of bitmaps (excluding the background) |
| 82 | background present (1 = the first ILBM is the background) |

### FRAM

| Offset | Content |
|---|---|
| 0 | mode: 1 = start of a repeated block |
| 2 | (long) last keyframe of the block, counted from 0 |
| 6 | (byte) number of times the block is played |
| 7 | unknown |
| 8 | number of in-between frames |
| 10, 12 | sound commands on two channels: index of the sound to start, -1 nothing, -2 change the playing sound (volume/pitch), -3 stop |
| 14 | number of `POLY` chunks that follow |
| 16 | keyframe palette: 32 `$0RGB` words |
| 80–85 | three words read by the player, purpose unknown (always 0 in the samples) |

### 8SVX and SEFX

The sounds are standard `FORM 8SVX` samples (8-bit signed, rate in `VHDR`). Each is followed by a 40-byte `SEFX` chunk holding its default settings. Inside a keyframe, one `SEFX` follows the `FRAM` for each channel that has a command (channel A first, then B), and gives the settings to apply at that keyframe:

| Offset | Content |
|---|---|
| 0 | low byte: volume 0–63; high byte: flag (unknown) |
| 2 | unknown (0, 4, 6) |
| 4 | 256 = play once, 0 = loop (as interpreted here) |
| 6 | Amiga period: rate = 3579545 / period (matches the `VHDR` rate) |
| 8 | number of samples to play (32 bits) |
| 12 | sound name, 20 bytes (`<Stop Sound>` for a stop command) |

### POLY

An 18-word header, followed by *n* (x, y) pairs.

| Word | Content |
|---|---|
| 0 | number of points *n* |
| 1 | type (low byte) and flags (high byte), see below |
| 2 | colour index |
| 3–6 | bounding box x1, y1, x2, y2 |
| 7 | identifier (not used for matching) |
| 8 | high byte: dot size (types 4 and 5); low byte: border style (0 = plain) |
| 9 | low byte: second colour, for the border (flag `$4000`) |
| 10–12 | bitmap: index, width, height (the point is the centre of the bitmap) |
| 13–15 | text: string length, ?, font size << 8 (low byte unknown) |

Types, as dispatched by the player's drawing routine:

| Type | Drawn as |
|---|---|
| 0 | filled polygon |
| 1 | line through the points (open) |
| 2 | outline (closed) |
| 3 | text: the two points are the corners of the text box |
| 4 | round dots of the given size on each point |
| 5 | square dots of the given size on each point |
| 6 | the bitmap stamped on each point |
| 7 | bitmap |

Flags: `$1000` the bitmap is drawn through a mask (colour 0 transparent, otherwise it is copied as an opaque rectangle); `$2000` the object is also painted into the background on each step, leaving a trail; `$4000` border in the second colour (polygon outline, or a line joining the dots or stamps). An object whose flags word is exactly `$7000` is never drawn.

Two consecutive keyframes are matched object by object, by their position in the list.

## Open questions

- Whether the in-between count of a keyframe applies towards the next keyframe or from the previous one.
- The playback speed of the original player.
- Thick or patterned borders (low byte of `POLY` word 8), drawn here as a plain 1-pixel border.
- Role of the invisible `$7000` objects.
- In `SEFX`: the high byte of the volume word, the word at offset 2, and whether offset 4 really means one-shot versus loop.

Comparing with FantaPlayer under emulation, or reading more of its code, would settle them. Contributions are welcome.

## Movies (`movies/`)

The `movies/` folder contains original `FORM FANT` files: the demo movies of the Amiga Fantavision disks (*!AmigaFantTitle*, *!FantMovie*, *Aliens*, *Anthropology*, *Atoms*, *Bird*, *BoingTheMovie*, *CarCrash!*, *CoolJay*, *Dinosaurs*, *Eye*, *Eyes*, *Jaws*, *LightHouse*, *Marilyn*, *NewYork*, *Party*, *VideoPie*, *WindyCove*) and *PA* (352×276, a PAL size, possibly not a Broderbund movie).

Fantavision and its demo movies are © Broderbund.
