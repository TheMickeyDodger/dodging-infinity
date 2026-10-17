#!/usr/bin/env node
/**
 * EXTRACTS the seven generated source atlases (OpenAI image_gen, see ASSET-SOURCES.json in the
 * asset library) into runtime furniture sprites — NON-DESTRUCTIVELY: the source PNGs are only
 * read, each is verified against its pinned SHA-256 first, and every output is a new file.
 *
 *   node scripts/extract-atlas-sprites.mjs --atlases <dir>              # write sprites + provenance
 *   node scripts/extract-atlas-sprites.mjs --atlases <dir> --inspect    # detection report only
 *
 * Output (deterministic for the same source bytes):
 *   webview-ui/public/assets/furniture/<ID>/{<ID>.png, manifest.json}   (the existing per-type
 *     layout and manifest schema, 16 px per tile; every sprite is 'decor' — none is a chair, so
 *     none creates a seat — except where SPEC marks a small item canPlaceOnSurfaces)
 *   packaging/di/ASSET-PROVENANCE.json   per sprite: source atlas file + its SHA-256, the source
 *     rectangle, scale, footprint / collision rows, and the alpha / edge QA numbers measured here.
 *
 * Pipeline, per object:
 *   1. DETECT: an alpha >= DETECT_ALPHA mask, dilated by MERGE_RADIUS so an object's own near bits
 *      join it; connected components; fragments below MIN_AREA attach to the nearest object within
 *      ATTACH_DIST or are dropped (counted). Rows are clustered by centre height into the prompt's
 *      row count and sorted left to right; a row holding MORE pieces than the prompt's motifs (a
 *      multi-part motif: a table with its chairs, a school of fish, a ring of logs) merges its
 *      horizontally CLOSEST neighbours until the count matches — atlas gutters are far wider than
 *      a motif's own gaps, and every merge is logged with its gap. The per-row counts must then
 *      equal SPEC's, or the script refuses to write anything.
 *   2. SAMPLE (premultiplied, alpha-aware area average): a target pixel's COVERAGE is the mean
 *      source alpha over its footprint; its COLOUR is averaged ONLY from source pixels with
 *      alpha >= SOLID_ALPHA (falling back to alpha-weighted, never unweighted). Alpha-0 pixels —
 *      the atlases' gutters carry nonzero hidden RGB under alpha 0 — therefore contribute
 *      nothing: the hidden matte can never be baked in.
 *   3. CLEAN: coverage >= COVER_CUT becomes opaque, else transparent (crisp pixel-art edges, no
 *      semi-transparent halo); isolated opaque pixels are removed and pinholes filled; boundary
 *      pixels are darkened (OUTLINE) so silhouettes stay readable at game scale.
 *   4. NORMALISE: 5-bit-per-channel palette quantisation; size from SPEC's target height in game
 *      pixels (16 px per tile), bottom-centred on a footprint-aligned canvas.
 *   5. METADATA: footprint = the canvas in tiles; the bottom `solid` rows collide, the rows above
 *      are backgroundTiles (walk-behind, never false-blocked); provenance as above.
 *   6. PASSAGE (per type, from the DRAWN art — collision from "the bottom opaque rows" alone made
 *      every arch a wall and every bridge a dam):
 *        opening — arches. Each 16 px column of the bottom tile row is classified from the output
 *          pixels: opaque fraction < OPENING_MAX_FILL AND a solid column on both sides is the drawn
 *          OPENING (passable); otherwise a solid leg (an empty edge column is margin, not a gap). The sprite is SPLIT into one furniture type per run of columns (<ID>_P1..),
 *          leg pieces colliding on their solid rows, opening pieces all walk-under (as the forest
 *          gateway's crown already is). No opening column found -> recorded, stays solid.
 *        span — bridges and piers: the sprite never collides (all rows walk-over); the WORLD lays
 *          walkable deck tiles under it along the recorded traversal axis, water either side stays
 *          non-walkable. Route tests prove bank-to-bank crossings.
 *        closed — gates drawn CLOSED: no drawn opening, so they stay solid and are never placed on
 *          a route.
 *   7. CONTRAST: a sprite whose mean luminance is below DARK_LUMA (a black dog, a black-moor
 *      goldfish) gets a LIGHT rim instead of the darkened outline, so it separates from dark
 *      ground as well as light; recorded per sprite as qa.rim.
 * Nothing here animates anything: every output is a single static frame.
 */
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { PNG } from 'pngjs';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const FURNITURE = path.join(ROOT, 'webview-ui/public/assets/furniture');
const PROVENANCE = path.join(ROOT, 'packaging/di/ASSET-PROVENANCE.json');
const TILE = 16;
const DETECT_ALPHA = 200;
const MERGE_RADIUS = 2;
const MIN_AREA = 600;
const ATTACH_DIST = 36;
const SOLID_ALPHA = 200;
const COVER_CUT = 0.5;
const OUTLINE = 0.72;
/** In the measured gap of the mean-luminance distribution: the three black animals sit at
 *  0.132-0.152, everything else at >= 0.168 (see review.json qa.meanLuma). */
const DARK_LUMA = 0.16;
const LIGHT_RIM = [176, 178, 190];
// 0.8 (was 0.5): at 0.5 the rim of the three dark companions (black Labrador, Bombay, black moor)
// barely separated them from the darkest floor the connected world uses — found in the per-crop
// art QA's companion ground scene (Amendments 8B/24); 0.8 separates them on every ground there.
const LIGHT_RIM_MIX = 0.8;
const OPENING_MAX_FILL = 0.35;

/** Pinned source atlases (ASSET-SOURCES.json). Rows: [ID, name, targetHeightPx, solidRows?, opts?] */
const SPEC = [
  {
    file: 'city-buildings-source.png',
    sha256: '43916389361fc4404d9266f70d6ab1474790b45ac9a18b219cca3c839493a52f',
    pack: 'city',
    rows: [
      [
        ['CITY_SKYSCRAPER_GLASS', 'Glass Skyscraper', 192, 4],
        ['CITY_SKYSCRAPER_DECO', 'Art-Deco Skyscraper', 192, 4],
        ['CITY_SUPERTALL', 'Slender Supertall', 208, 3],
        ['CITY_TOWER_ROUND', 'Round Office Tower', 150, 4],
        ['CITY_TOWER_GARDEN', 'Vertical-Garden Tower', 150, 4],
        ['CITY_CLOCK_TOWER', 'Brick Clock Tower', 150, 3],
      ],
      [
        ['CITY_TOWN_HALL', 'Town Hall', 104, 3],
        ['CITY_LIBRARY', 'Grand Library', 96, 3],
        ['CITY_FIRE_STATION', 'Fire Station', 104, 3],
        ['CITY_TRAIN_STATION', 'Train Station', 80, 2],
        ['CITY_OBSERVATORY', 'Observatory', 100, 3],
        ['CITY_THEATRE', 'Deco Theatre', 104, 3],
      ],
      [
        ['CITY_CAFE', 'Corner Cafe', 88, 3],
        ['CITY_BAKERY', 'Bakery', 88, 3],
        ['CITY_TOWNHOUSE', 'Brownstone Townhouse', 96, 3],
        ['CITY_SHOP_ROW', 'Row of Shops', 88, 3],
        ['CITY_WAREHOUSE', 'Warehouse', 84, 3],
        ['CITY_MARKET_HALL', 'Glass Market Hall', 96, 3],
      ],
      [
        ['CITY_HOTEL', 'Boutique Hotel', 112, 3],
        ['CITY_APARTMENTS', 'Rooftop-Garden Apartments', 124, 3],
        ['CITY_CHAPEL', 'Stone Chapel', 96, 3],
        ['CITY_WORKSHOP', 'Brick Workshop', 84, 3],
        ['CITY_TRANSIT_TERMINAL', 'Transit Terminal', 68, 2],
        ['CITY_MUSEUM', 'Columned Museum', 92, 3],
      ],
    ],
  },
  {
    file: 'farmstead.png',
    sha256: '881e9b93f5465e55439a80c63e69b496e3f029aac8d82193dca8a2134c38b127',
    pack: 'farmstead',
    rows: [
      [
        ['FARM_BARN', 'Red Barn', 80, 3],
        ['FARM_STABLE', 'Stone Stable', 60, 2],
        ['FARM_FARMHOUSE', 'Farmhouse', 76, 3],
        ['FARM_SILO', 'Grain Silo', 86, 2],
        ['FARM_WINDMILL', 'Windmill', 86, 2],
        ['FARM_COOP', 'Chicken Coop', 42, 2],
      ],
      [
        ['FARM_GREENHOUSE', 'Greenhouse', 60, 2],
        ['FARM_PRODUCE_STAND', 'Produce Stand', 40, 1],
        ['FARM_WATER_TOWER', 'Water Tower', 76, 2],
        ['FARM_WELL', 'Rustic Well', 40, 1],
        ['FARM_GATE', 'Farm Gate', 26, 1, { passage: 'closed' }],
        ['FARM_COTTAGE', 'Stone Cottage', 60, 2],
      ],
      [
        ['FARM_HAY_ROUND', 'Round Hay Bale', 22, 1],
        ['FARM_HAY_STACK', 'Stacked Hay Bales', 22, 1],
        ['FARM_BARRELS', 'Wooden Barrels', 20, 1],
        ['FARM_GRAIN_SACK', 'Grain Sack', 14, 1],
        ['FARM_TROUGH', 'Feed Trough', 14, 1],
        ['FARM_HANDCART', 'Handcart', 22, 1],
      ],
      [
        ['FARM_TRACTOR', 'Green Tractor', 28, 1],
        ['FARM_TRAILER', 'Farm Trailer', 22, 1],
        ['FARM_WHEELBARROW', 'Wheelbarrow', 16, 1],
        ['FARM_FIREWOOD', 'Stacked Firewood', 20, 1],
        ['FARM_WATERING_CAN', 'Watering Can', 11, 1],
        ['FARM_BEEHIVES', 'Beehives', 24, 1],
      ],
      [
        ['FARM_PUMPKINS', 'Pumpkin Patch', 20, 1],
        ['FARM_WHEAT', 'Wheat', 26, 1],
        ['FARM_CORN', 'Corn', 30, 1],
        ['FARM_APPLE_TREE', 'Apple Tree', 56, 1],
        ['FARM_SCARECROW', 'Scarecrow', 34, 1],
        ['FARM_HARVEST_CRATES', 'Harvest Crates', 24, 1],
      ],
    ],
  },
  {
    file: 'farm-animals.png',
    sha256: '2a9fa11c02dc6dabde97e7b2af3e31f073af15664a943bc2a64e7cd023c4cfdc',
    pack: 'farm-animals',
    rows: [
      [
        ['ANIMAL_COW_HOLSTEIN', 'Dairy Cow', 24, 1],
        ['ANIMAL_COW_BROWN', 'Brown Cow', 24, 1],
        ['ANIMAL_CALF', 'Calf', 17, 1],
        ['ANIMAL_HIGHLAND_COW', 'Highland Cow', 24, 1],
        ['ANIMAL_PIG', 'Spotted Pig', 17, 1],
        ['ANIMAL_PIGLET', 'Piglet', 11, 1],
      ],
      [
        ['ANIMAL_DONKEY_L', 'Donkey', 24, 1],
        ['ANIMAL_DONKEY_R', 'Donkey (facing right)', 24, 1],
        ['ANIMAL_HORSE_CHESTNUT_L', 'Chestnut Horse', 30, 1],
        ['ANIMAL_HORSE_CHESTNUT_R', 'Chestnut Horse (facing right)', 30, 1],
        ['ANIMAL_HORSE_WHITE', 'White Horse', 30, 1],
        ['ANIMAL_HORSE_BAY', 'Bay Horse', 30, 1],
      ],
      [
        ['ANIMAL_PONY', 'Pony', 24, 1],
        ['ANIMAL_SHEEP', 'Sheep', 19, 1],
        ['ANIMAL_LAMB', 'Lamb', 14, 1],
        ['ANIMAL_GOAT_WHITE', 'White Goat', 20, 1],
        ['ANIMAL_GOAT_BROWN', 'Brown Goat', 20, 1],
        ['ANIMAL_ALPACA', 'Alpaca', 28, 1],
      ],
      [
        ['ANIMAL_CHICKEN', 'Chicken', 12, 1],
        ['ANIMAL_ROOSTER', 'Rooster', 15, 1],
        ['ANIMAL_CHICK', 'Chick', 8, 1],
        ['ANIMAL_DUCK', 'Mallard Duck', 12, 1],
        ['ANIMAL_GOOSE', 'Goose', 15, 1],
        ['ANIMAL_TURKEY', 'Turkey', 17, 1],
      ],
      [
        ['ANIMAL_FARM_DOG', 'Farm Dog', 17, 1],
        ['ANIMAL_BARN_CAT', 'Barn Cat', 13, 1],
        ['ANIMAL_RABBIT_WHITE', 'White Rabbit', 11, 1],
        ['ANIMAL_RABBIT_BROWN', 'Brown Rabbit', 11, 1],
        ['ANIMAL_PEACOCK', 'Peacock', 22, 1],
        ['ANIMAL_DOG_SLEEPING', 'Sleeping Dog', 11, 1],
      ],
    ],
  },
  {
    file: 'world-details.png',
    sha256: '29a5b1159a958494c158e44aea506028d749e2ff6769dcdfebe73756e53f20ce',
    pack: 'world-details',
    rows: [
      [
        ['DETAIL_LAMP_ORNATE', 'Ornate Streetlamp', 36, 1],
        ['DETAIL_LAMP_MODERN', 'Modern Streetlamp', 36, 1],
        ['DETAIL_HYDRANT', 'Fire Hydrant', 13, 1],
        ['DETAIL_MAILBOX', 'Mailbox', 18, 1],
        ['DETAIL_BIN', 'Wheeled Bin', 16, 1],
        ['DETAIL_PHONE_BOOTH', 'Telephone Booth', 36, 1],
        ['DETAIL_PARK_BENCH', 'Park Bench', 18, 1],
        ['DETAIL_CAFE_TABLE', 'Cafe Table Set', 18, 1],
      ],
      [
        ['DETAIL_FOUNTAIN_SQUARE', 'Square Fountain', 30, 2],
        ['DETAIL_FOUNTAIN_TIERED', 'Tiered Fountain', 38, 2],
        ['DETAIL_SCULPTURE', 'Bronze Sculpture', 32, 1],
        ['DETAIL_OBELISK', 'Obelisk', 42, 1],
        ['DETAIL_PLANTER', 'Flower Planter', 16, 1],
        ['DETAIL_BIKE_RACK', 'Bicycle Rack', 14, 1],
        ['DETAIL_BICYCLE', 'Bicycle', 16, 1],
        ['DETAIL_KIOSK', 'News Kiosk', 36, 2],
      ],
      [
        ['DETAIL_BRIDGE_STONE', 'Stone Bridge', 48, 1, { passage: 'span', axis: 'ew' }],
        ['DETAIL_FOOTBRIDGE', 'Wooden Footbridge', 44, 1, { passage: 'span', axis: 'ew' }],
        ['DETAIL_GARDEN_ARCH', 'Garden Arch', 62, 1, { passage: 'opening' }],
        ['DETAIL_IRON_GATE', 'Wrought-Iron Gate', 34, 1, { passage: 'closed' }],
        ['DETAIL_BRICK_WALL', 'Brick Wall', 18, 1],
        ['DETAIL_FENCE', 'Rustic Fence', 16, 1],
        ['DETAIL_STAIRS', 'Stone Stairs', 30, 1],
        ['DETAIL_SIGNPOST', 'Signpost', 26, 1],
      ],
      [
        ['DETAIL_TENT', 'Campsite Tent', 26, 1],
        ['DETAIL_FIRE_RING', 'Stone Fire Ring', 14, 1],
        ['DETAIL_PICNIC_TABLE', 'Picnic Table', 18, 1],
        ['DETAIL_LOOKOUT_SIGN', 'Lookout Signpost', 26, 1],
        ['DETAIL_VIEWER', 'Binocular Viewer', 24, 1],
        ['DETAIL_TELESCOPE', 'Telescope', 24, 1],
        ['DETAIL_BACKPACK', 'Hiking Backpack', 14, 1],
        ['DETAIL_LOG_CIRCLE', 'Log Seating Circle', 18, 1],
      ],
      [
        ['DETAIL_RUIN_ARCH', 'Stone Ruin Arch', 40, 1, { passage: 'opening' }],
        ['DETAIL_STANDING_STONES', 'Standing Stones', 30, 1],
        ['DETAIL_TREASURE_CHEST', 'Treasure Chest', 11, 1],
        ['DETAIL_MUSHROOMS', 'Mushrooms', 14, 1],
        ['DETAIL_HOLLOW_LOG', 'Hollow Log', 14, 1],
        ['DETAIL_LILY_POND', 'Lily Pond', 20, 1],
        ['DETAIL_BIRDHOUSE', 'Birdhouse', 26, 1],
        ['DETAIL_DEER_STATUE', 'Deer Statue', 38, 1],
      ],
      [
        ['DETAIL_TAXI', 'Taxi', 20, 1],
        ['DETAIL_VAN', 'Delivery Van', 24, 1],
        ['DETAIL_SCOOTER', 'Scooter', 16, 1],
        ['DETAIL_CONE', 'Traffic Cone', 11, 1],
        ['DETAIL_BARRIER', 'Construction Barrier', 18, 1],
        ['DETAIL_PALLET', 'Pallet', 10, 1],
        ['DETAIL_BOXES', 'Stacked Boxes', 16, 1],
        ['DETAIL_RAIN_BARREL', 'Rain Barrel', 20, 1],
      ],
    ],
  },
  {
    file: 'river-and-coast-source.png',
    sha256: 'e3bacff5b9e9cfa8799472fde9978c2cb9afc1851346700b4d1c7f81ab63957a',
    pack: 'river-and-coast',
    rows: [
      [
        ['COAST_RIVER_STRAIGHT', 'River Segment', 48, 3],
        ['COAST_RIVER_BEND_L', 'River Bend (left)', 48, 3],
        ['COAST_RIVER_BEND_R', 'River Bend (right)', 48, 3],
        ['COAST_RIVER_JUNCTION', 'River Junction', 48, 3],
        ['COAST_SPRING', 'River Source Spring', 48, 3],
        ['COAST_WATERFALL', 'Waterfall', 64, 4],
        ['COAST_POOL', 'Calm Pool', 48, 3],
        ['COAST_STEPPING_STONES', 'Stepping Stones', 48, 3],
      ],
      [
        ['COAST_BEACH_STRAIGHT', 'Beach Shoreline', 48, 3],
        ['COAST_BEACH_INNER', 'Beach Inner Corner', 48, 3],
        ['COAST_BEACH_OUTER', 'Beach Outer Corner', 48, 3],
        ['COAST_BANK_STRAIGHT', 'Grassy Bank', 48, 3],
        ['COAST_BANK_INNER', 'Grassy Bank Inner Corner', 48, 3],
        ['COAST_BANK_OUTER', 'Grassy Bank Outer Corner', 48, 3],
        ['COAST_ROCKY', 'Rocky Coastline', 48, 3],
        ['COAST_WAVES', 'Foamy Shore Waves', 48, 3],
      ],
      [
        ['COAST_ROWBOAT', 'Rowboat', 28, 2],
        ['COAST_SAILBOAT', 'Sailboat', 44, 2],
        ['COAST_FISHING_BOAT', 'Fishing Boat', 34, 2],
        ['COAST_CANOE', 'Canoe', 30, 2],
        ['COAST_LIGHTHOUSE', 'Lighthouse', 72, 2],
        ['COAST_PIER_STRAIGHT', 'Pier', 40, 3, { passage: 'span', axis: 'ns' }],
        ['COAST_PIER_END', 'Pier End', 40, 3, { passage: 'span', axis: 'ns' }],
        ['COAST_PIER_CORNER', 'Pier Corner', 40, 3, { passage: 'span', axis: 'corner' }],
      ],
      [
        ['COAST_BOLLARD', 'Mooring Bollard', 14, 1],
        ['COAST_ROPE', 'Coiled Rope', 11, 1],
        ['COAST_DOCK_POST', 'Dock Post', 18, 1],
        ['COAST_LOBSTER_POTS', 'Lobster Pots', 18, 1],
        ['COAST_NET_RACK', 'Fishing Net Rack', 30, 1],
        ['COAST_FISH_STALL', 'Fish Market Stall', 36, 1],
        ['COAST_LIFE_RING', 'Life Ring', 14, 1],
        ['COAST_UMBRELLA', 'Beach Umbrella', 30, 1],
      ],
      [
        ['COAST_ROCKS', 'Seaside Rocks', 24, 1],
        ['COAST_TIDEPOOL', 'Tidepool', 20, 1],
        ['COAST_REEDS', 'Reeds', 20, 1],
        ['COAST_CATTAILS', 'Cattails', 22, 1],
        ['COAST_DRIFTWOOD', 'Driftwood', 12, 1],
        ['COAST_SHELLS', 'Shell Cluster', 14, 1],
        ['COAST_BUOY', 'Buoy', 16, 1],
        ['COAST_HARBOUR_WALL', 'Harbour Wall', 20, 1],
      ],
    ],
  },
  {
    file: 'aquarium.png',
    sha256: 'f75341c2e6aad5b2f04efe37fa212e7e62a71337f1e86a935f9e6f179e56c7a0',
    pack: 'aquarium',
    rows: [
      [
        ['AQUA_TANK_PANORAMIC', 'Panoramic Tank', 40, 2],
        ['AQUA_TANK_CURVED', 'Curved Corner Tank', 40, 2],
        ['AQUA_TANK_CYLINDER', 'Cylinder Tank', 56, 2],
        ['AQUA_TANK_DESK', 'Desk Fish Tank', 20, 1, { surface: true }],
        ['AQUA_JELLY_CYLINDER', 'Jellyfish Cylinder', 52, 2],
        ['AQUA_TUNNEL_ARCH', 'Tunnel Viewing Arch', 52, 1, { passage: 'opening' }],
        ['AQUA_TOUCH_POOL', 'Touch Pool', 30, 2],
        ['AQUA_TANK_REEF', 'Open-Top Reef Tank', 34, 2],
      ],
      [
        ['AQUA_CLOWNFISH', 'Clownfish', 10, 1],
        ['AQUA_BLUE_TANG', 'Blue Tang', 10, 1],
        ['AQUA_BUTTERFLYFISH', 'Butterflyfish', 12, 1],
        ['AQUA_FISH_SCHOOL', 'Silver Fish School', 14, 1],
        ['AQUA_KOI', 'Koi', 11, 1],
        ['AQUA_SEAHORSE', 'Seahorse', 16, 1],
        ['AQUA_MANTA', 'Manta Ray', 22, 1],
        ['AQUA_SEA_TURTLE', 'Sea Turtle', 16, 1],
      ],
      [
        ['AQUA_SHARK', 'Reef Shark', 18, 1],
        ['AQUA_JELLY_PINK', 'Pink Jellyfish', 20, 1],
        ['AQUA_JELLY_BLUE', 'Blue Jellyfish', 20, 1],
        ['AQUA_OCTOPUS', 'Octopus', 20, 1],
        ['AQUA_STARFISH', 'Starfish', 13, 1],
        ['AQUA_CRAB', 'Crab', 12, 1],
        ['AQUA_LOBSTER', 'Lobster', 16, 1],
        ['AQUA_PUFFERFISH', 'Pufferfish', 13, 1],
      ],
      [
        ['AQUA_CORAL_RED', 'Red Coral', 20, 1],
        ['AQUA_CORAL_FAN', 'Fan Coral', 22, 1],
        ['AQUA_CORAL_BRAIN', 'Brain Coral', 16, 1],
        ['AQUA_CORAL_TUBE', 'Tube Coral', 20, 1],
        ['AQUA_ANEMONE', 'Sea Anemone', 18, 1],
        ['AQUA_KELP', 'Kelp', 26, 1],
        ['AQUA_SEAGRASS', 'Seagrass', 16, 1],
        ['AQUA_LILY_PADS', 'Lily Pads', 14, 1],
      ],
      [
        ['AQUA_BOULDER', 'Aquarium Boulder', 26, 1],
        ['AQUA_RUIN_ARCH', 'Underwater Ruin Arch', 42, 1, { passage: 'opening' }],
        ['AQUA_TREASURE', 'Treasure Chest', 18, 1],
        ['AQUA_SHIPWRECK', 'Shipwreck Section', 30, 1],
        ['AQUA_AERATOR', 'Bubble Aerator', 34, 1],
        ['AQUA_BENCH', 'Exhibit Bench', 18, 1],
        ['AQUA_INFO_PEDESTAL', 'Information Pedestal', 26, 1],
        ['AQUA_STANCHION', 'Velvet Rope Stanchion', 20, 1],
      ],
    ],
  },
  {
    file: 'pets-and-companions.png',
    sha256: 'abb717839c27fd94acaf16b9ffb48ba881c927f0c853da1ac8a8d17bbb4aa343',
    pack: 'pets-and-companions',
    rows: [
      [
        ['PET_GOLDEN_RETRIEVER', 'Golden Retriever', 18, 1],
        ['PET_LABRADOR', 'Black Labrador', 18, 1],
        ['PET_CORGI', 'Corgi', 14, 1],
        ['PET_DACHSHUND', 'Dachshund', 13, 1],
        ['PET_HUSKY', 'Husky', 18, 1],
        ['PET_POODLE', 'Poodle', 18, 1],
      ],
      [
        ['PET_BEAGLE', 'Beagle', 15, 1],
        ['PET_DALMATIAN', 'Dalmatian', 18, 1],
        ['PET_BORDER_COLLIE', 'Border Collie', 17, 1],
        ['PET_SHIBA', 'Shiba Inu', 15, 1],
        ['PET_PUG', 'Pug', 13, 1],
        ['PET_GERMAN_SHEPHERD', 'German Shepherd', 19, 1],
      ],
      [
        ['PET_MAINE_COON', 'Maine Coon', 15, 1],
        ['PET_SIAMESE', 'Siamese Cat', 14, 1],
        ['PET_BRITISH_SHORTHAIR', 'British Shorthair', 13, 1],
        ['PET_RAGDOLL', 'Ragdoll Cat', 14, 1],
        ['PET_BENGAL', 'Bengal Cat', 14, 1],
        ['PET_SPHYNX', 'Sphynx Cat', 14, 1],
      ],
      [
        ['PET_PERSIAN', 'Persian Cat', 13, 1],
        ['PET_NORWEGIAN_FOREST', 'Norwegian Forest Cat', 15, 1],
        ['PET_SCOTTISH_FOLD', 'Scottish Fold', 12, 1],
        ['PET_BOMBAY', 'Bombay Cat', 13, 1],
        ['PET_RUSSIAN_BLUE', 'Russian Blue', 13, 1],
        ['PET_ABYSSINIAN', 'Abyssinian Cat', 14, 1],
      ],
      [
        ['PET_GOLDFISH_ORANGE', 'Orange Fancy Goldfish', 9, 1],
        ['PET_GOLDFISH_RED_WHITE', 'Red-and-White Goldfish', 9, 1],
        ['PET_GOLDFISH_BLACK_MOOR', 'Black Moor Goldfish', 9, 1],
        ['PET_RABBIT_LOP', 'Lop-Eared Rabbit', 12, 1],
        ['PET_RABBIT_SPOTTED', 'Spotted Rabbit', 12, 1],
        ['PET_GUINEA_PIG', 'Guinea Pig', 10, 1],
      ],
      [
        ['PET_HAMSTER', 'Hamster', 9, 1],
        ['PET_PARROT', 'Green Parrot', 12, 1],
        ['PET_COCKATIEL', 'Cockatiel', 13, 1],
        ['PET_TURTLE', 'Pet Turtle', 9, 1],
        ['PET_FERRET', 'Ferret', 10, 1],
        ['PET_HEDGEHOG', 'Hedgehog', 9, 1],
      ],
    ],
  },
];

// ── detection ───────────────────────────────────────────────────────────────
/**
 * Components of the alpha >= DETECT_ALPHA mask (dilated by MERGE_RADIUS), each with its bbox,
 * area and mean colour. Objects (area >= MIN_AREA) are clustered into ROWS FIRST; fragments are
 * then given an owner by ownFragments(), never by proximity alone.
 */
function detect(png, nRows) {
  const { width: W, height: H, data } = png;
  const mask = new Uint8Array(W * H);
  for (let i = 0; i < W * H; i++) mask[i] = data[i * 4 + 3] >= DETECT_ALPHA ? 1 : 0;
  const tmp = new Uint8Array(W * H);
  for (let y = 0; y < H; y++)
    for (let x = 0; x < W; x++) {
      let v = 0;
      for (let d = -MERGE_RADIUS; d <= MERGE_RADIUS && !v; d++) {
        const xx = x + d;
        if (xx >= 0 && xx < W && mask[y * W + xx]) v = 1;
      }
      tmp[y * W + x] = v;
    }
  const dil = new Uint8Array(W * H);
  for (let y = 0; y < H; y++)
    for (let x = 0; x < W; x++) {
      let v = 0;
      for (let d = -MERGE_RADIUS; d <= MERGE_RADIUS && !v; d++) {
        const yy = y + d;
        if (yy >= 0 && yy < H && tmp[yy * W + x]) v = 1;
      }
      dil[y * W + x] = v;
    }
  const label = new Int32Array(W * H).fill(-1);
  const comps = [];
  const stack = [];
  for (let s0 = 0; s0 < W * H; s0++) {
    if (!dil[s0] || label[s0] >= 0) continue;
    const id = comps.length;
    const c = {
      id,
      x0: W,
      y0: H,
      x1: -1,
      y1: -1,
      area: 0,
      sx: 0,
      sy: 0,
      rgb: [0, 0, 0],
      comps: [id],
      attachedFragments: [],
      merges: [],
    };
    label[s0] = id;
    stack.push(s0);
    while (stack.length) {
      const p = stack.pop();
      const x = p % W;
      const y = (p - x) / W;
      if (mask[p]) {
        c.area++;
        c.sx += x;
        c.sy += y;
        c.rgb[0] += data[p * 4];
        c.rgb[1] += data[p * 4 + 1];
        c.rgb[2] += data[p * 4 + 2];
        if (x < c.x0) c.x0 = x;
        if (y < c.y0) c.y0 = y;
        if (x > c.x1) c.x1 = x;
        if (y > c.y1) c.y1 = y;
      }
      for (const q of [p - 1, p + 1, p - W, p + W]) {
        if (q < 0 || q >= W * H) continue;
        if ((q === p - 1 && x === 0) || (q === p + 1 && x === W - 1)) continue;
        if (dil[q] && label[q] < 0) {
          label[q] = id;
          stack.push(q);
        }
      }
    }
    comps.push(c.area > 0 ? c : null);
  }
  const all = comps.filter(Boolean);
  for (const c of all) {
    c.mean = c.rgb.map((v) => v / c.area);
    c.cx = c.sx / c.area;
    c.cy = (c.y0 + c.y1) / 2;
  }
  const big = all.filter((c) => c.area >= MIN_AREA);
  const fragments = all.filter((c) => c.area < MIN_AREA);
  // ROWS are decided by the motifs themselves, BEFORE any fragment can move a bbox
  const rows = intoRows(big, nRows);
  rows.forEach((r, i) => r.forEach((o) => (o.row = i)));
  const { droppedFragments } = ownFragments(png, label, mask, rows, fragments);
  return { rows, objects: big, droppedFragments, label };
}

const boxDist = (a, b) =>
  Math.hypot(Math.max(0, b.x0 - a.x1, a.x0 - b.x1), Math.max(0, b.y0 - a.y1, a.y0 - b.y1));
const rgbDist = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);

/**
 * FRAGMENT OWNERSHIP (the Amendment-7 correction). Proximity alone decided ownership before, and a
 * row boundary split the aerator's bubble column, handing its upper half to the anemone. Now:
 *   1. COLUMN CONTINUITY: fragments are first chained to EACH OTHER (single linkage within
 *      FRAGMENT_LINK px), so a run of bubbles, spilled grain or a rain chain travels as ONE cluster.
 *   2. ROW OWNERSHIP: the cluster belongs to the row band (the vertical extent of that row's
 *      motifs) it overlaps most; only motifs IN THAT ROW may own it. Attachment never crosses a
 *      row-cluster boundary.
 *   3. CHROMATIC COHERENCE: among that row's motifs within ATTACH_DIST, the owner is the one whose
 *      OWN pixels near the cluster (LOCAL_RING px around it) are closest in mean RGB; if even the
 *      best is farther than COLOUR_MAX the cluster is DROPPED (recorded, flagged), never forced.
 *   Every decision is recorded with its row, distances and colour distances (owner and runner-up).
 */
const FRAGMENT_LINK = 30;
const LOCAL_RING = 24;
const COLOUR_MAX = 90;
function ownFragments(png, label, mask, rows, fragments) {
  const { width: W, data } = png;
  // 1. chain fragments into clusters
  const parent = fragments.map((_, i) => i);
  const find = (i) => (parent[i] === i ? i : (parent[i] = find(parent[i])));
  for (let i = 0; i < fragments.length; i++)
    for (let j = i + 1; j < fragments.length; j++)
      if (boxDist(fragments[i], fragments[j]) <= FRAGMENT_LINK) parent[find(i)] = find(j);
  const groups = new Map();
  fragments.forEach((f, i) => {
    const k = find(i);
    if (!groups.has(k)) groups.set(k, []);
    groups.get(k).push(f);
  });
  const bands = rows.map((r) => ({
    y0: Math.min(...r.map((o) => o.y0)),
    y1: Math.max(...r.map((o) => o.y1)),
  }));
  const droppedFragments = [];
  for (const members of groups.values()) {
    const cl = {
      x0: Math.min(...members.map((f) => f.x0)),
      y0: Math.min(...members.map((f) => f.y0)),
      x1: Math.max(...members.map((f) => f.x1)),
      y1: Math.max(...members.map((f) => f.y1)),
    };
    const area = members.reduce((n, f) => n + f.area, 0);
    const mean = [0, 1, 2].map((k) => members.reduce((n, f) => n + f.rgb[k], 0) / area);
    // 2. the row band it overlaps most (ties: the nearest band centre)
    let row = 0;
    let best = -Infinity;
    bands.forEach((b, i) => {
      const overlap = Math.min(b.y1, cl.y1) - Math.max(b.y0, cl.y0);
      const centreGap = -Math.abs((b.y0 + b.y1) / 2 - (cl.y0 + cl.y1) / 2) / 1e6;
      if (overlap + centreGap > best) {
        best = overlap + centreGap;
        row = i;
      }
    });
    const crossRowNearer = rows
      .flat()
      .filter((o) => o.row !== row && boxDist(cl, o) <= ATTACH_DIST)
      .map((o) => ({ row: o.row, distPx: Math.round(boxDist(cl, o)) }));
    // 3. colour coherence against each in-row candidate's OWN pixels near the cluster
    const candidates = rows[row]
      .filter((o) => boxDist(cl, o) <= ATTACH_DIST)
      .map((o) => {
        let n = 0;
        const sum = [0, 0, 0];
        const own = new Set(o.comps);
        for (let y = Math.max(o.y0, cl.y0 - LOCAL_RING); y <= Math.min(o.y1, cl.y1 + LOCAL_RING); y++)
          for (let x = Math.max(o.x0, cl.x0 - LOCAL_RING); x <= Math.min(o.x1, cl.x1 + LOCAL_RING); x++) {
            const p = y * W + x;
            if (!mask[p] || !own.has(label[p])) continue;
            sum[0] += data[p * 4];
            sum[1] += data[p * 4 + 1];
            sum[2] += data[p * 4 + 2];
            n++;
          }
        const local = n > 0 ? sum.map((v) => v / n) : o.mean;
        return { o, distPx: boxDist(cl, o), colourDist: rgbDist(mean, local), localPixels: n };
      })
      .sort((a, b) => a.colourDist - b.colourDist);
    const rect = { x: cl.x0, y: cl.y0, w: cl.x1 - cl.x0 + 1, h: cl.y1 - cl.y0 + 1 };
    const decision = {
      area,
      rect,
      pieces: members.length,
      row: row + 1,
      meanRgb: mean.map(Math.round),
      candidates: candidates.map((c) => ({
        at: { x: c.o.x0, y: c.o.y0 },
        distPx: Math.round(c.distPx),
        colourDist: Math.round(c.colourDist),
      })),
      crossRowWithinReach: crossRowNearer,
    };
    const owner = candidates[0];
    if (owner && owner.colourDist <= COLOUR_MAX) {
      const o = owner.o;
      o.x0 = Math.min(o.x0, cl.x0);
      o.y0 = Math.min(o.y0, cl.y0);
      o.x1 = Math.max(o.x1, cl.x1);
      o.y1 = Math.max(o.y1, cl.y1);
      o.area += area;
      o.comps.push(...members.map((f) => f.id));
      o.attachedFragments.push({
        ...decision,
        distPx: Math.round(owner.distPx),
        colourDist: Math.round(owner.colourDist),
        runnerUpColourDist: candidates[1] ? Math.round(candidates[1].colourDist) : null,
      });
    } else
      droppedFragments.push({
        ...decision,
        reason: owner ? `colour-incoherent (best ${Math.round(owner.colourDist)} > ${COLOUR_MAX})` : 'no in-row motif within reach',
        nearestDistPx: owner ? Math.round(owner.distPx) : null,
        nearest: owner ? owner.o : rows[row][0],
      });
  }
  return { droppedFragments };
}

/** Cluster objects into `n` rows by vertical centre (1-D k-means from evenly spaced seeds). */
function intoRows(objects, n) {
  const ys = objects.map((o) => o.cy).sort((a, b) => a - b);
  let centres = Array.from({ length: n }, (_, i) => ys[Math.floor(((i + 0.5) / n) * ys.length)]);
  let rows = [];
  for (let iter = 0; iter < 50; iter++) {
    rows = centres.map(() => []);
    for (const o of objects) {
      let k = 0;
      for (let j = 1; j < n; j++)
        if (Math.abs(o.cy - centres[j]) < Math.abs(o.cy - centres[k])) k = j;
      rows[k].push(o);
    }
    const next = rows.map((r, j) => (r.length ? r.reduce((s, o) => s + o.cy, 0) / r.length : centres[j]));
    if (next.every((c, j) => c === centres[j])) break;
    centres = next;
  }
  return rows.map((r) => r.sort((a, b) => a.cx - b.cx));
}

const mergeBoxes = (a, b, gapPx) => ({
  ...a,
  comps: [...a.comps, ...b.comps],
  attachedFragments: [...a.attachedFragments, ...b.attachedFragments],
  merges: [
    ...a.merges,
    ...b.merges,
    {
      gapPx,
      parts: [a, b].map((o) => ({ x: o.x0, y: o.y0, w: o.x1 - o.x0 + 1, h: o.y1 - o.y0 + 1, cx: Math.round(o.cx) })),
    },
  ],
  x0: Math.min(a.x0, b.x0),
  y0: Math.min(a.y0, b.y0),
  x1: Math.max(a.x1, b.x1),
  y1: Math.max(a.y1, b.y1),
  area: a.area + b.area,
  sx: a.sx + b.sx,
  cx: (a.sx + b.sx) / (a.area + b.area),
  cy: (Math.min(a.y0, b.y0) + Math.max(a.y1, b.y1)) / 2,
});

/** Merge a row's horizontally closest neighbours until it holds `expected` pieces. */
function mergeToCount(row, expected, log) {
  while (row.length > expected) {
    let best = 0;
    let gap = Infinity;
    for (let i = 0; i + 1 < row.length; i++) {
      const g = row[i + 1].x0 - row[i].x1;
      if (g < gap) {
        gap = g;
        best = i;
      }
    }
    log.push({ mergedAtX: row[best].x1, gapPx: gap });
    row.splice(best, 2, mergeBoxes(row[best], row[best + 1], gap));
  }
  return row;
}

// ── sampling, cleaning, normalising ─────────────────────────────────────────
function extractObject(png, label, o, targetH) {
  const { width: W, data } = png;
  const own = new Set(o.comps);
  /** Alpha of a source pixel AS PART OF THIS OBJECT: pixels of any other object, of a dropped
   *  fragment, or of no object at all count as fully transparent (no neighbour contamination). */
  const alphaOf = (x, y) => (own.has(label[y * W + x]) ? data[(y * W + x) * 4 + 3] : 0);
  let foreignInRect = 0;
  const bw = o.x1 - o.x0 + 1;
  const bh = o.y1 - o.y0 + 1;
  const scale = targetH / bh;
  const sw = Math.max(1, Math.round(bw * scale));
  const sh = Math.max(1, Math.round(bh * scale));
  const fw = Math.max(1, Math.ceil(sw / TILE));
  const fh = Math.max(1, Math.ceil(sh / TILE));
  const cw = fw * TILE;
  const ch = fh * TILE;
  const ox = Math.floor((cw - sw) / 2);
  const oy = ch - sh;
  const cover = new Float32Array(cw * ch);
  const rgb = new Float32Array(cw * ch * 3);
  let hiddenUnderAlpha0 = 0;
  let semiTransparent = 0;
  for (let y = o.y0; y <= o.y1; y++)
    for (let x = o.x0; x <= o.x1; x++) {
      const i = (y * W + x) * 4;
      const a = data[i + 3];
      if (a === 0 && (data[i] | data[i + 1] | data[i + 2])) hiddenUnderAlpha0++;
      else if (a > 0 && a < 255) semiTransparent++;
      if (a >= DETECT_ALPHA && !own.has(label[y * W + x])) foreignInRect++;
    }
  for (let ty = 0; ty < sh; ty++)
    for (let tx = 0; tx < sw; tx++) {
      const sx0 = o.x0 + tx / scale;
      const sx1 = o.x0 + (tx + 1) / scale;
      const sy0 = o.y0 + ty / scale;
      const sy1 = o.y0 + (ty + 1) / scale;
      let aSum = 0;
      let n = 0;
      let solid = [0, 0, 0, 0];
      let weighted = [0, 0, 0, 0];
      for (let y = Math.floor(sy0); y < Math.ceil(sy1); y++)
        for (let x = Math.floor(sx0); x < Math.ceil(sx1); x++) {
          if (x < o.x0 || x > o.x1 || y < o.y0 || y > o.y1) {
            n++;
            continue;
          }
          const i = (y * W + x) * 4;
          const a = alphaOf(x, y);
          n++;
          aSum += a;
          if (a === 0) continue; // hidden RGB under alpha 0 never contributes
          if (a >= SOLID_ALPHA) {
            solid[0] += data[i];
            solid[1] += data[i + 1];
            solid[2] += data[i + 2];
            solid[3]++;
          }
          weighted[0] += data[i] * a;
          weighted[1] += data[i + 1] * a;
          weighted[2] += data[i + 2] * a;
          weighted[3] += a;
        }
      const p = (oy + ty) * cw + ox + tx;
      cover[p] = n ? aSum / n / 255 : 0;
      const src = solid[3] > 0 ? solid.map((v) => v / solid[3]) : weighted.map((v) => v / Math.max(1, weighted[3]));
      rgb[p * 3] = src[0];
      rgb[p * 3 + 1] = src[1];
      rgb[p * 3 + 2] = src[2];
    }
  // binarise coverage
  const on = new Uint8Array(cw * ch);
  for (let p = 0; p < cw * ch; p++) on[p] = cover[p] >= COVER_CUT ? 1 : 0;
  const nb = (x, y) => {
    let k = 0;
    for (let dy = -1; dy <= 1; dy++)
      for (let dx = -1; dx <= 1; dx++) {
        if (!dx && !dy) continue;
        const xx = x + dx;
        const yy = y + dy;
        if (xx >= 0 && yy >= 0 && xx < cw && yy < ch && on[yy * cw + xx]) k++;
      }
    return k;
  };
  let isolatedRemoved = 0;
  let holesFilled = 0;
  const next = on.slice();
  for (let y = 0; y < ch; y++)
    for (let x = 0; x < cw; x++) {
      const k = nb(x, y);
      const p = y * cw + x;
      if (on[p] && k <= 1) {
        next[p] = 0;
        isolatedRemoved++;
      } else if (!on[p] && k >= 7 && cover[p] > 0) {
        next[p] = 1;
        holesFilled++;
      }
    }
  on.set(next);
  // contrast: mean luminance of the opaque pixels decides the rim (dark outline vs light rim)
  let lumSum = 0;
  let lumN = 0;
  for (let p = 0; p < cw * ch; p++)
    if (on[p]) {
      lumSum += (0.2126 * rgb[p * 3] + 0.7152 * rgb[p * 3 + 1] + 0.0722 * rgb[p * 3 + 2]) / 255;
      lumN++;
    }
  const meanLuma = lumN ? lumSum / lumN : 0;
  const rim = meanLuma < DARK_LUMA ? 'light' : 'dark';
  // output RGBA: quantise to 5 bits/channel; rim the silhouette boundary
  const out = new PNG({ width: cw, height: ch });
  let opaque = 0;
  for (let y = 0; y < ch; y++)
    for (let x = 0; x < cw; x++) {
      const p = y * cw + x;
      const i = p * 4;
      if (!on[p]) {
        out.data[i] = out.data[i + 1] = out.data[i + 2] = out.data[i + 3] = 0;
        continue;
      }
      opaque++;
      const edge =
        x === 0 ||
        y === 0 ||
        x === cw - 1 ||
        y === ch - 1 ||
        !on[p - 1] ||
        !on[p + 1] ||
        !on[p - cw] ||
        !on[p + cw];
      for (let c = 0; c < 3; c++) {
        const v = rgb[p * 3 + c];
        const shaded = !edge ? v : rim === 'dark' ? v * OUTLINE : v + (LIGHT_RIM[c] - v) * LIGHT_RIM_MIX;
        out.data[i + c] = Math.min(255, Math.round(shaded / 8) * 8);
      }
      out.data[i + 3] = 255;
    }
  return {
    png: out,
    width: cw,
    height: ch,
    footprintW: fw,
    footprintH: fh,
    scale,
    drawn: { x: ox, y: oy, w: sw, h: sh },
    qa: {
      opaquePixels: opaque,
      isolatedRemoved,
      holesFilled,
      sourceHiddenRgbUnderAlpha0: hiddenUnderAlpha0,
      sourceSemiTransparent: semiTransparent,
      foreignPixelsExcludedFromRect: foreignInRect,
      meanLuma: Math.round(meanLuma * 1000) / 1000,
      rim,
    },
  };
}

// ── main ────────────────────────────────────────────────────────────────────
const QA_REASONS = new Set([
  'merged-distinct-motifs',
  'missing-piece',
  'stray-fragment',
  'wrong-label',
  'bad-edge-or-fringe',
  'misaligned-baseline',
  'wrong-scale',
]);
const args = process.argv.slice(2);
const flag = (name) => (args.includes(name) ? args[args.indexOf(name) + 1] : undefined);
const atlasDir = flag('--atlases');
const inspect = args.includes('--inspect');
const reviewDir = flag('--review-dir');
const qaFile = flag('--qa') ?? path.join(ROOT, 'scripts/atlas-sprite-qa.json');
const quarantineDir = flag('--quarantine-dir');
if (!atlasDir) {
  console.error(
    'usage: extract-atlas-sprites.mjs --atlases <dir> [--inspect | --review-dir <dir> | --quarantine-dir <dir>] [--qa <verdicts.json>]',
  );
  process.exit(2);
}
const sha = (buf) => crypto.createHash('sha256').update(buf).digest('hex');
const rectOf = (o) => ({ x: o.x0, y: o.y0, w: o.x1 - o.x0 + 1, h: o.y1 - o.y0 + 1 });
const problems = [];
const planned = [];
const atlasReports = [];
for (const atlas of SPEC) {
  const bytes = fs.readFileSync(path.join(atlasDir, atlas.file));
  const digest = sha(bytes);
  if (digest !== atlas.sha256) {
    console.error(`REFUSING: ${atlas.file} sha256 ${digest} != pinned ${atlas.sha256}`);
    process.exit(1);
  }
  const png = PNG.sync.read(bytes);
  const { rows: detectedRows, objects, droppedFragments, label } = detect(png, atlas.rows.length);
  const merges = [];
  const rows = detectedRows.map((r, i) => mergeToCount(r, atlas.rows[i].length, merges));
  const counts = rows.map((r) => r.length);
  const expected = atlas.rows.map((r) => r.length);
  const ok = counts.every((c, i) => c === expected[i]);
  // the measured inter-motif GUTTERS (after merging): what a merge gap is compared against
  const gutters = rows.flatMap((r) => r.slice(1).map((o, i) => o.x0 - r[i].x1));
  const minGutter = gutters.length ? Math.min(...gutters) : 0;
  const attachedCount = objects.reduce((n, o) => n + o.attachedFragments.length, 0);
  console.log(
    `${atlas.file}: ${objects.length} pieces (fragments attached ${attachedCount}, dropped ${droppedFragments.length}; multi-part merges ${merges.length}${merges.length ? ' gaps ' + merges.map((m) => m.gapPx).join(',') : ''}; min inter-motif gutter ${minGutter}px); rows ${counts.join('/')} expected ${expected.join('/')} ${ok ? 'OK' : 'MISMATCH'}`,
  );
  if (inspect || !ok)
    rows.forEach((r, i) =>
      console.log(
        `   row ${i + 1}: ` +
          r.map((o) => `[${o.x0},${o.y0} ${o.x1 - o.x0 + 1}x${o.y1 - o.y0 + 1} a${o.area}]`).join(' '),
      ),
    );
  if (!ok) {
    problems.push(atlas.file);
    continue;
  }
  atlasReports.push({
    file: atlas.file,
    sha256: digest,
    width: png.width,
    height: png.height,
    piecesDetected: objects.length,
    multiPartMerges: merges.length,
    mergeGapsPx: merges.map((m) => m.gapPx),
    gutterMinPx: minGutter,
    gutterMedianPx: gutters.sort((a, b) => a - b)[Math.floor(gutters.length / 2)] ?? null,
    fragmentsAttached: attachedCount,
    fragmentsDropped: droppedFragments.map((f) => ({ area: f.area, rect: f.rect, nearestDistPx: f.nearestDistPx, reason: f.reason, row: f.row, meanRgb: f.meanRgb })),
  });
  const finals = rows.flat();
  // GRID-COLUMN CHECK (label-shift detector): the prompt asks for a regular grid, so a motif's
  // centre should fall in the grid column of its ordinal. Generated atlases are not perfectly
  // regular (world-details row 1 is not), so a mismatch is a MANDATORY-REVIEW flag, not a refusal.
  const colOf = (cx, n) => Math.floor(cx / (png.width / n)) + 1;
  rows.forEach((r, ri) =>
    r.forEach((o, ci) => {
      o.gridColumn = colOf(o.cx, r.length);
      o.gridColumnMatchesOrdinal = o.gridColumn === ci + 1;
      for (const m of o.merges) {
        m.partGridColumns = m.parts.map((part) => colOf(part.cx, r.length));
        // GUTTER-LIKE = the pieces sit in DIFFERENT grid columns: the merge may have fused two
        // motifs. Geometry decides this, not a gap number (see the measured distribution).
        m.gutterLike = new Set(m.partGridColumns).size > 1;
      }
    }),
  );
  rows.forEach((r, ri) =>
    r.forEach((o, ci) => {
      // dropped fragments reported against the motif they lie nearest to
      const nearbyDropped = droppedFragments
        .filter((f) => f.nearest && finals.find((g) => g.comps.includes(f.nearest.id)) === o)
        .map((f) => ({ area: f.area, rect: f.rect, nearestDistPx: f.nearestDistPx, reason: f.reason, row: f.row, meanRgb: f.meanRgb }));
      planned.push({
        atlas,
        png,
        label,
        digest,
        o,
        spec: atlas.rows[ri][ci],
        grid: [ri + 1, ci + 1],
        minGutter,
        nearbyDropped,
      });
    }),
  );
}
if (problems.length) {
  console.error(`REFUSING to write: detection does not match the prompt grid for ${problems.join(', ')}`);
  process.exit(1);
}
if (inspect) process.exit(0);

/** One crop's full record (everything the QA sheet shows and the provenance keeps). */
function record({ atlas, png, label, digest, o, spec, grid, nearbyDropped }) {
  const [id, name, targetH, solidRows = 1, opts = {}] = spec;
  const r = extractObject(png, label, o, targetH);
  const solid = Math.min(solidRows, r.footprintH);
  const merges = o.merges;
  const reviewFlags = [
    ...merges.map(
      (m) => `merge gap ${m.gapPx}px, grid columns ${m.partGridColumns.join('/')}${m.gutterLike ? ' (GUTTER-LIKE: pieces in different grid columns)' : ''}`,
    ),
    ...o.attachedFragments.map(
      (f) => `attached ${f.pieces}-piece fragment cluster ${f.area}px at ${f.distPx}px (row ${f.row}, colour dist ${f.colourDist}${f.runnerUpColourDist !== null ? ', runner-up ' + f.runnerUpColourDist : ''}${f.crossRowWithinReach.length ? ', cross-row motif within reach EXCLUDED' : ''})`,
    ),
    ...nearbyDropped.map((f) => `dropped fragment cluster ${f.area}px: ${f.reason}`),
    ...(o.gridColumnMatchesOrdinal ? [] : [`centre in grid column ${o.gridColumn}, ordinal ${grid[1]}: verify identity`]),
  ];
  const manifest = {
    id,
    name,
    category: 'decor',
    type: 'asset',
    canPlaceOnWalls: false,
    canPlaceOnSurfaces: !!opts.surface,
    backgroundTiles: r.footprintH - solid,
    width: r.width,
    height: r.height,
    footprintW: r.footprintW,
    footprintH: r.footprintH,
  };
  // PASSAGE (see header step 6)
  const passage = { kind: opts.passage ?? 'solid' };
  let pieces = [];
  if (opts.passage === 'span') {
    manifest.backgroundTiles = r.footprintH; // never collides: the world's deck tiles carry it
    passage.axis = opts.axis;
    passage.rule = `walk-over span: no collision cells; the world lays walkable deck tiles under all ${r.footprintW}x${r.footprintH} footprint cells along axis ${opts.axis}; water outside the deck stays non-walkable`;
  } else if (opts.passage === 'closed') {
    passage.rule = 'drawn CLOSED (no drawn opening): solid like any decor; never placed on a route';
  } else if (opts.passage === 'opening') {
    const cols = [];
    for (let c = 0; c < r.footprintW; c++) {
      let n = 0;
      for (let y = r.height - TILE; y < r.height; y++)
        for (let x = c * TILE; x < (c + 1) * TILE; x++) if (r.png.data[(y * r.width + x) * 4 + 3]) n++;
      const fill = n / (TILE * TILE);
      cols.push({ col: c, bottomRowFill: Math.round(fill * 1000) / 1000, passable: fill < OPENING_MAX_FILL });
    }
    // an OPENING lies BETWEEN legs: a low-fill column at the sprite's edge is margin, not a passage
    for (const c of cols) {
      const solidLeft = cols.some((d) => d.col < c.col && !d.passable);
      const solidRight = cols.some((d) => d.col > c.col && !d.passable);
      if (c.passable && !(solidLeft && solidRight)) {
        c.passable = false;
        c.note = 'edge margin, not between legs';
      }
    }
    passage.columns = cols;
    passage.openingColumns = cols.filter((c) => c.passable).map((c) => c.col);
    if (passage.openingColumns.length === 0) {
      passage.rule = `no bottom-row column under ${OPENING_MAX_FILL} fill: no drawn opening at 16 px resolution, stays solid`;
    } else {
      // one furniture type per run of same-class columns
      const runs = [];
      for (const c of cols) {
        const last = runs[runs.length - 1];
        if (last && last.passable === c.passable) last.c1 = c.col + 1;
        else runs.push({ c0: c.col, c1: c.col + 1, passable: c.passable });
      }
      pieces = runs.map((run, k) => {
        const w = (run.c1 - run.c0) * TILE;
        const piece = new PNG({ width: w, height: r.height });
        for (let y = 0; y < r.height; y++)
          for (let x = 0; x < w; x++) {
            const si = (y * r.width + run.c0 * TILE + x) * 4;
            const di = (y * w + x) * 4;
            for (let q = 0; q < 4; q++) piece.data[di + q] = r.png.data[si + q];
          }
        const pid = `${id}_P${k + 1}`;
        const bytes = PNG.sync.write(piece);
        return {
          id: pid,
          columns: [run.c0, run.c1],
          passable: run.passable,
          offsetCols: run.c0,
          pngBytes: bytes,
          manifest: {
            ...manifest,
            id: pid,
            name: `${name} (${run.passable ? 'opening' : 'leg'} ${k + 1}/${runs.length})`,
            width: w,
            footprintW: run.c1 - run.c0,
            backgroundTiles: run.passable ? r.footprintH : r.footprintH - solid,
          },
        };
      });
      passage.rule = `split into ${pieces.length} types: ${pieces.map((pc) => `${pc.id} cols ${pc.columns[0]}-${pc.columns[1] - 1} ${pc.passable ? 'OPENING (all walk-under)' : `leg (bottom ${solid} row solid)`}`).join('; ')}`;
    }
  }
  const pngBytes = PNG.sync.write(r.png);
  return {
    manifest,
    pngBytes,
    pieces,
    provenance: {
      id,
      name,
      pack: atlas.pack,
      sourceAtlas: atlas.file,
      sourceAtlasSha256: digest,
      sourceGridCell: { row: grid[0], col: grid[1] },
      sourceRect: rectOf(o),
      scale: Math.round(r.scale * 10000) / 10000,
      drawnRect: r.drawn,
      footprint: {
        w: r.footprintW,
        h: r.footprintH,
        solidRows: solid,
        backgroundTiles: r.footprintH - solid,
      },
      collision:
        passage.kind === 'span'
          ? passage.rule
          : pieces.length
            ? passage.rule
            : `bottom ${solid} row(s) x ${r.footprintW} col(s) block; ${r.footprintH - solid} row(s) above are walk-behind`,
      passage,
      runtimeTypes: pieces.length ? pieces.map((pc) => ({ id: pc.id, offsetCols: pc.offsetCols, passable: pc.passable, outputSha256: sha(pc.pngBytes) })) : [{ id, offsetCols: 0 }],
      category: 'decor',
      canPlaceOnSurfaces: !!opts.surface,
      animated: false,
      merges,
      gridColumn: o.gridColumn,
      gridColumnMatchesOrdinal: o.gridColumnMatchesOrdinal,
      attachedFragments: o.attachedFragments,
      droppedFragmentsNearby: nearbyDropped,
      reviewFlags,
      qa: r.qa,
      outputSha256: sha(pngBytes),
    },
  };
}

const records = planned.map(record);
const pipeline = {
  mergeGutterLikeRule:
    'a merge is GUTTER-LIKE when its pieces have centres in different grid columns (prompt grid: atlas width / motifs in the row). The former bbox-gap threshold (-14 px, the minimum measured inter-motif gutter) could not discriminate: measured intra-motif merge gaps (-83..0 px) and inter-motif gutters (-14..+60 px) overlap.',
  fragmentOwnership: { FRAGMENT_LINK, LOCAL_RING, COLOUR_MAX, rule: 'chain fragments; own by row band; in-row candidates within ATTACH_DIST; least local mean-RGB distance; drop when > COLOUR_MAX' },
  DETECT_ALPHA,
  MERGE_RADIUS,
  MIN_AREA,
  ATTACH_DIST,
  SOLID_ALPHA,
  COVER_CUT,
  OUTLINE,
  DARK_LUMA,
  LIGHT_RIM,
  LIGHT_RIM_MIX,
  OPENING_MAX_FILL,
  quantisation: '5 bits per channel',
  tile: TILE,
};

if (reviewDir) {
  // REVIEW: every crop, written OUTSIDE the runtime assets, for the visual QA sheets.
  fs.mkdirSync(path.join(reviewDir, 'sprites'), { recursive: true });
  for (const { manifest, pngBytes, pieces } of records) {
    fs.writeFileSync(path.join(reviewDir, 'sprites', `${manifest.id}.png`), pngBytes);
    for (const pc of pieces) fs.writeFileSync(path.join(reviewDir, 'sprites', `${pc.id}.png`), pc.pngBytes);
  }
  fs.writeFileSync(
    path.join(reviewDir, 'review.json'),
    JSON.stringify(
      { pipeline, atlases: atlasReports, crops: records.map((r) => ({ ...r.provenance, manifest: r.manifest })) },
      null,
      2,
    ) + '\n',
  );
  console.log(`review: ${records.length} crops -> ${reviewDir}`);
  process.exit(0);
}

// NORMAL: only crops with a recorded visual-QA PASS verdict become runtime assets. Verdicts:
//   'verified'   — EXAMINED in the per-crop art QA (rendered at in-world scale over the ground it
//                  is placed on) and passed: verified usable, and admitted;
//   'pass'       — admitted to the catalogue but NOT examined (provisional; never counted usable);
//   'quarantine' — retained outside the runtime catalogue, never placed (with a reason).
const admitted = (v) => v.verdict === 'pass' || v.verdict === 'verified';
const verdicts = JSON.parse(fs.readFileSync(qaFile, 'utf-8')).verdicts;
const missing = records.filter((r) => !verdicts[r.manifest.id]);
const invalid = records.filter((r) => {
  const v = verdicts[r.manifest.id];
  return v && !(admitted(v) || (v.verdict === 'quarantine' && QA_REASONS.has(v.reason)));
});
if (missing.length || invalid.length) {
  console.error(
    `REFUSING: crops without a valid visual-QA verdict in ${path.relative(ROOT, qaFile)}: ${[...missing, ...invalid].map((r) => r.manifest.id).join(', ')}`,
  );
  process.exit(1);
}
const quarantined = records.filter((r) => verdicts[r.manifest.id].verdict === 'quarantine');
if (quarantined.length && !quarantineDir) {
  console.error('REFUSING: quarantined crops exist; pass --quarantine-dir <dir> to retain them');
  process.exit(1);
}
const provenance = [];
for (const r of records) {
  const v = verdicts[r.manifest.id];
  const runtimeDir = path.join(FURNITURE, r.manifest.id);
  if (admitted(v)) {
    const types = r.pieces.length ? r.pieces : [r];
    if (r.pieces.length) fs.rmSync(runtimeDir, { recursive: true, force: true }); // the unsplit sprite would be a wall
    for (const t of types) {
      const dir = path.join(FURNITURE, t.manifest.id);
      fs.mkdirSync(dir, { recursive: true });
      fs.writeFileSync(path.join(dir, `${t.manifest.id}.png`), t.pngBytes);
      fs.writeFileSync(path.join(dir, 'manifest.json'), JSON.stringify(t.manifest, null, 2) + '\n');
    }
  } else {
    for (const pc of r.pieces) fs.rmSync(path.join(FURNITURE, pc.id), { recursive: true, force: true });
    // retained non-destructively OUTSIDE the runtime catalogue; never placed in the world
    fs.rmSync(runtimeDir, { recursive: true, force: true });
    const qdir = path.join(quarantineDir, r.manifest.id);
    fs.mkdirSync(qdir, { recursive: true });
    fs.writeFileSync(path.join(qdir, `${r.manifest.id}.png`), r.pngBytes);
    fs.writeFileSync(path.join(qdir, 'manifest.json'), JSON.stringify(r.manifest, null, 2) + '\n');
  }
  provenance.push({ ...r.provenance, qaVerdict: v.verdict, ...(v.reason ? { qaReason: v.reason } : {}), ...(v.note ? { qaNote: v.note } : {}) });
}
const passed = provenance.filter((p) => p.qaVerdict === 'pass' || p.qaVerdict === 'verified');
const verified = provenance.filter((p) => p.qaVerdict === 'verified');
const qaDoc = JSON.parse(fs.readFileSync(qaFile, 'utf-8'));
fs.writeFileSync(
  PROVENANCE,
  JSON.stringify(
    {
      generatedBy: 'scripts/extract-atlas-sprites.mjs',
      note: 'Static single-frame sprites extracted from the seven OpenAI image_gen source atlases. None is animated. Source PNGs are read-only inputs and are never modified. Only qaVerdict "verified" (examined in the per-crop art QA and passed) and "pass" (admitted, NOT examined) crops are runtime assets; quarantined crops are retained outside the runtime catalogue and never placed.',
      counts: {
        requestedMotifs: 248,
        specCells: SPEC.reduce((n, a) => n + a.rows.flat().length, 0),
        cropsExtracted: records.length,
        // Only a crop EXAMINED in the per-crop art QA may be called usable: admission is not it.
        catalogueAdmitted: passed.length,
        verifiedUsable: verified.length,
        admittedNotExamined: passed.length - verified.length,
        quarantined: quarantined.length,
        qaStatus: qaDoc.status,
      },
      qaVerdicts: path.relative(ROOT, qaFile),
      pipeline,
      atlases: atlasReports,
      sprites: provenance,
    },
    null,
    2,
  ) + '\n',
);
console.log(
  `wrote ${passed.length} admitted sprites (runtime; ${verified.length} verified usable), ${quarantined.length} quarantined, and ${path.relative(ROOT, PROVENANCE)}`,
);
