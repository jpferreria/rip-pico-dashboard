/* Minimal offline ISO/IEC 18004 QR Code SVG generator (Versions 1-10, Byte mode, ECC-L) */
(function (global) {
  // [totalCodewords, ecPerBlock, numBlocksGroup1, dataPerBlock1, numBlocksGroup2, dataPerBlock2]
  const V_INFO = [
    null,
    [26, 7, 1, 19, 0, 0],
    [44, 10, 1, 34, 0, 0],
    [70, 15, 1, 55, 0, 0],
    [100, 20, 1, 80, 0, 0],
    [134, 26, 1, 108, 0, 0],
    [172, 18, 2, 68, 0, 0],
    [196, 20, 2, 78, 0, 0],
    [242, 24, 2, 97, 0, 0],
    [292, 30, 2, 116, 0, 0],
    [346, 18, 2, 68, 2, 69],
  ];

  const ALIGN = [
    null, [], [6, 18], [6, 22], [6, 26], [6, 30],
    [6, 34], [6, 22, 38], [6, 24, 42], [6, 26, 46], [6, 28, 50],
  ];

  // GF(256) exp/log tables (poly 0x11d)
  const EXP = new Uint8Array(512);
  const LOG = new Uint8Array(256);
  for (let i = 0, x = 1; i < 255; i++) {
    EXP[i] = x;
    LOG[x] = i;
    x <<= 1;
    if (x & 0x100) x ^= 0x11d;
  }
  for (let i = 255; i < 512; i++) EXP[i] = EXP[i - 255];

  function gfMul(a, b) {
    return a === 0 || b === 0 ? 0 : EXP[LOG[a] + LOG[b]];
  }

  function rsGenerator(deg) {
    let poly = [1];
    for (let i = 0; i < deg; i++) {
      const next = new Array(poly.length + 1).fill(0);
      for (let j = 0; j < poly.length; j++) {
        next[j] ^= poly[j];
        next[j + 1] ^= gfMul(poly[j], EXP[i]);
      }
      poly = next;
    }
    return poly;
  }

  function rsEncode(data, ecLen) {
    const gen = rsGenerator(ecLen);
    const rem = new Uint8Array(ecLen);
    for (let i = 0; i < data.length; i++) {
      const factor = data[i] ^ rem[0];
      for (let j = 0; j < ecLen - 1; j++) rem[j] = rem[j + 1];
      rem[ecLen - 1] = 0;
      for (let j = 0; j < ecLen; j++) {
        rem[j] ^= gfMul(gen[j + 1], factor);
      }
    }
    return rem;
  }

  function encodeData(text) {
    const bytes = new TextEncoder().encode(text);
    let ver = 0;
    for (let v = 1; v <= 10; v++) {
      const info = V_INFO[v];
      const dataCap = info[2] * info[3] + info[4] * info[5];
      const lenBits = v <= 9 ? 8 : 16;
      const neededBits = 4 + lenBits + bytes.length * 8;
      if (neededBits <= dataCap * 8) {
        ver = v;
        break;
      }
    }
    if (!ver) throw new Error("QR payload too long");

    const info = V_INFO[ver];
    const dataCap = info[2] * info[3] + info[4] * info[5];
    const bits = [];
    const pushBits = (val, n) => {
      for (let i = n - 1; i >= 0; i--) bits.push((val >> i) & 1);
    };

    pushBits(0b0100, 4); // Byte mode
    pushBits(bytes.length, ver <= 9 ? 8 : 16);
    for (let i = 0; i < bytes.length; i++) pushBits(bytes[i], 8);

    // Terminator + byte alignment
    for (let i = 0; i < 4 && bits.length < dataCap * 8; i++) bits.push(0);
    while (bits.length % 8 !== 0) bits.push(0);

    const codewords = [];
    for (let i = 0; i < bits.length; i += 8) {
      let b = 0;
      for (let j = 0; j < 8; j++) b = (b << 1) | bits[i + j];
      codewords.push(b);
    }
    const pads = [0xec, 0x11];
    for (let i = 0; codewords.length < dataCap; i++) {
      codewords.push(pads[i & 1]);
    }

    // Split into RS blocks & interleave
    const ecLen = info[1];
    const blocks = [];
    const ecs = [];
    let offset = 0;
    for (let g = 0; g < 2; g++) {
      const count = g === 0 ? info[2] : info[4];
      const size = g === 0 ? info[3] : info[5];
      for (let b = 0; b < count; b++) {
        const blk = codewords.slice(offset, offset + size);
        offset += size;
        blocks.push(blk);
        ecs.push(rsEncode(blk, ecLen));
      }
    }

    const out = [];
    const maxData = Math.max(...blocks.map((b) => b.length));
    for (let i = 0; i < maxData; i++) {
      for (let b = 0; b < blocks.length; b++) {
        if (i < blocks[b].length) out.push(blocks[b][i]);
      }
    }
    for (let i = 0; i < ecLen; i++) {
      for (let b = 0; b < ecs.length; b++) out.push(ecs[b][i]);
    }
    return { ver, codewords: out };
  }

  function makeMatrix(ver, codewords) {
    const size = ver * 4 + 17;
    const m = Array.from({ length: size }, () => new Int8Array(size));
    const isFunc = Array.from({ length: size }, () => new Uint8Array(size));

    const setMod = (r, c, dark) => {
      if (r >= 0 && r < size && c >= 0 && c < size) {
        m[r][c] = dark ? 1 : 0;
        isFunc[r][c] = 1;
      }
    };

    const drawFinder = (r0, c0) => {
      for (let dr = -1; dr <= 7; dr++) {
        for (let dc = -1; dc <= 7; dc++) {
          const dist = Math.max(Math.abs(dr - 3), Math.abs(dc - 3));
          setMod(r0 + dr, c0 + dc, dist !== 2 && dist !== 4);
        }
      }
    };

    drawFinder(0, 0);
    drawFinder(0, size - 7);
    drawFinder(size - 7, 0);

    // Alignment patterns
    const centers = ALIGN[ver];
    for (let i = 0; i < centers.length; i++) {
      for (let j = 0; j < centers.length; j++) {
        const r = centers[i];
        const c = centers[j];
        if (isFunc[r][c]) continue;
        for (let dr = -2; dr <= 2; dr++) {
          for (let dc = -2; dc <= 2; dc++) {
            setMod(r + dr, c + dc, Math.max(Math.abs(dr), Math.abs(dc)) !== 1);
          }
        }
      }
    }

    // Timing patterns
    for (let i = 8; i < size - 8; i++) {
      if (!isFunc[6][i]) setMod(6, i, i % 2 === 0);
      if (!isFunc[i][6]) setMod(i, 6, i % 2 === 0);
    }

    // Dark module + reserve format info areas
    setMod(size - 8, 8, true);
    for (let i = 0; i < 9; i++) {
      if (!isFunc[8][i]) setMod(8, i, false);
      if (!isFunc[i][8]) setMod(i, 8, false);
    }
    for (let i = 0; i < 8; i++) {
      if (!isFunc[8][size - 1 - i]) setMod(8, size - 1 - i, false);
      if (!isFunc[size - 1 - i][8]) setMod(size - 1 - i, 8, false);
    }

    // Version info (v >= 7)
    if (ver >= 7) {
      let rem = ver;
      for (let i = 0; i < 12; i++) rem = (rem << 1) ^ ((rem >> 11) * 0x1f25);
      const bits = (ver << 12) | rem;
      for (let i = 0; i < 18; i++) {
        const dark = ((bits >> i) & 1) === 1;
        const a = size - 11 + (i % 3);
        const b = Math.floor(i / 3);
        setMod(a, b, dark);
        setMod(b, a, dark);
      }
    }

    // Place data bits in zigzag
    let bitIdx = 0;
    const totalBits = codewords.length * 8;
    for (let right = size - 1; right >= 1; right -= 2) {
      if (right === 6) right = 5;
      const upward = ((right + 1) & 2) === 0;
      for (let vert = 0; vert < size; vert++) {
        const r = upward ? size - 1 - vert : vert;
        for (let j = 0; j < 2; j++) {
          const c = right - j;
          if (!isFunc[r][c]) {
            let dark = 0;
            if (bitIdx < totalBits) {
              dark = (codewords[bitIdx >> 3] >> (7 - (bitIdx & 7))) & 1;
              bitIdx++;
            }
            m[r][c] = dark;
          }
        }
      }
    }

    // Apply mask 0 ((r + c) % 2 === 0) — simple and reliable; pick best of 8 masks
    const maskFn = [
      (r, c) => (r + c) % 2 === 0,
      (r, c) => r % 2 === 0,
      (r, c) => c % 3 === 0,
      (r, c) => (r + c) % 3 === 0,
      (r, c) => (Math.floor(r / 2) + Math.floor(c / 3)) % 2 === 0,
      (r, c) => ((r * c) % 2) + ((r * c) % 3) === 0,
      (r, c) => (((r * c) % 2) + ((r * c) % 3)) % 2 === 0,
      (r, c) => (((r + c) % 2) + ((r * c) % 3)) % 2 === 0,
    ];

    function applyFormat(mat, mask) {
      // ECC Level L = 01
      const data = (0b01 << 3) | mask;
      let rem = data;
      for (let i = 0; i < 10; i++) rem = (rem << 1) ^ ((rem >> 9) * 0x537);
      const bits = ((data << 10) | rem) ^ 0x5412;

      for (let i = 0; i <= 5; i++) mat[8][i] = (bits >> (14 - i)) & 1;
      mat[8][7] = (bits >> 8) & 1;
      mat[8][8] = (bits >> 7) & 1;
      mat[7][8] = (bits >> 6) & 1;
      for (let i = 9; i < 15; i++) mat[14 - i][8] = (bits >> (14 - i)) & 1;

      for (let i = 0; i < 7; i++) mat[size - 1 - i][8] = (bits >> (14 - i)) & 1;
      for (let i = 0; i < 8; i++) mat[8][size - 8 + i] = (bits >> (7 - i)) & 1;
    }

    let bestMask = 0;
    let bestScore = Infinity;
    let bestMat = null;

    for (let mask = 0; mask < 8; mask++) {
      const cand = m.map((row) => new Int8Array(row));
      const fn = maskFn[mask];
      let darkCount = 0;
      for (let r = 0; r < size; r++) {
        for (let c = 0; c < size; c++) {
          if (!isFunc[r][c] && fn(r, c)) cand[r][c] ^= 1;
          darkCount += cand[r][c];
        }
      }
      applyFormat(cand, mask);

      // Lightweight penalty score (runs + 2x2 blocks + balance)
      let score = Math.abs(Math.round((darkCount * 100) / (size * size)) - 50) * 2;
      for (let r = 0; r < size; r++) {
        let runR = 1, runC = 1;
        for (let c = 1; c < size; c++) {
          runR = cand[r][c] === cand[r][c - 1] ? runR + 1 : 1;
          if (runR === 5) score += 3; else if (runR > 5) score += 1;
          runC = cand[c][r] === cand[c - 1][r] ? runC + 1 : 1;
          if (runC === 5) score += 3; else if (runC > 5) score += 1;
        }
      }
      for (let r = 0; r < size - 1; r++) {
        for (let c = 0; c < size - 1; c++) {
          const s = cand[r][c] + cand[r][c + 1] + cand[r + 1][c] + cand[r + 1][c + 1];
          if (s === 0 || s === 4) score += 3;
        }
      }
      if (score < bestScore) {
        bestScore = score;
        bestMask = mask;
        bestMat = cand;
      }
    }
    return bestMat;
  }

  function toSvgElement(text, px = 220) {
    const { ver, codewords } = encodeData(text);
    const mat = makeMatrix(ver, codewords);
    const size = mat.length;
    const quiet = 4;
    const total = size + quiet * 2;

    const ns = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", `0 0 ${total} ${total}`);
    svg.setAttribute("width", String(px));
    svg.setAttribute("height", String(px));
    svg.setAttribute("role", "img");
    svg.setAttribute("shape-rendering", "crispEdges");

    const bg = document.createElementNS(ns, "rect");
    bg.setAttribute("width", String(total));
    bg.setAttribute("height", String(total));
    bg.setAttribute("fill", "#ffffff");
    svg.appendChild(bg);

    let d = "";
    for (let r = 0; r < size; r++) {
      for (let c = 0; c < size; c++) {
        if (mat[r][c]) {
          d += `M${c + quiet},${r + quiet}h1v1h-1z`;
        }
      }
    }
    const path = document.createElementNS(ns, "path");
    path.setAttribute("d", d);
    path.setAttribute("fill", "#000000");
    svg.appendChild(path);
    return svg;
  }

  global.PicoQR = { toSvgElement };
})(window);
