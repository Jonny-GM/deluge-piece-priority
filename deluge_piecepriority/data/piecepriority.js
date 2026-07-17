/**
 * piecepriority.js
 *
 * Torrent-details tab: per-piece state visualization and priority
 * controls. Calls piecepriority.* directly via deluge.client's dynamic
 * RPC proxy -- every method here is one already registered on the
 * daemon by deluge_piecepriority/core.py (docs/spec/01-core-rpc.md);
 * this file re-implements no RPC logic of its own.
 */

Ext.namespace('Deluge.piecepriority');

// Deluge's WebUI plugin loader (deluge/ui/web/pluginmanager.py's
// gather_info()) only ever reads a WebPluginBase's `scripts` attribute --
// `stylesheets` is declared on the base class but nothing consumes it, so
// external CSS never actually gets linked into the page. Injecting styles
// here instead, once, when this script first loads.
(function () {
    const css = [
        '.piecepriority-legend { padding: 2px 0 8px 0; }',
        '.piecepriority-legend-item { display: inline-block; margin-right: 14px; font-size: 11px; color: #333; }',
        '.piecepriority-legend-swatch { display: inline-block; width: 10px; height: 10px; margin-right: 4px; vertical-align: middle; border: 1px solid rgba(0, 0, 0, 0.25); }',
        '.piecepriority-canvas { display: block; cursor: pointer; }',
        '.piecepriority-info { padding: 6px 0 0 0; color: #666; font-size: 11px; }',
        '.piecepriority-tooltip { position: absolute; z-index: 20000; background: #333; color: #fff; padding: 2px 6px; font-size: 11px; border-radius: 3px; pointer-events: none; white-space: nowrap; }',
    ].join('\n');
    const style = document.createElement('style');
    style.type = 'text/css';
    style.appendChild(document.createTextNode(css));
    document.head.appendChild(style);
})();

// Deluge's own stock core.get_torrent_status 'pieces' field already encodes
// per-piece state as 0 (missing) / 1 (available from a peer, not yet
// requested) / 2 (currently downloading) / 3 (have) -- see
// deluge/core/torrent.py:_get_pieces_info. No custom RPC method needed to
// read this.
const PIECE_STATE_HAVE = 3;
const PIECE_STATE_DOWNLOADING = 2;
// 1 = a connected peer has the piece; 0 = no connected peer has it.
const PIECE_STATE_UNAVAILABLE = 0;

// libtorrent's 0-7 piece-priority scale, bucketed for display: 0 will
// never download (skipped), 7 is the top priority streaming-style callers
// assign to the pieces they need next. In between renders as ordinary
// "missing".
const PIECE_PRIORITY_SKIP = 0;
const PIECE_PRIORITY_URGENT = 7;

// Matches Deluge's own FILE_PRIORITY convention (deluge-all.js) so the
// menu reads the same as the stock file-priority right-click menu.
const PRIORITY_OPTIONS = [
    { text: _('Skip'), value: 0 },
    { text: _('Low'), value: 1 },
    { text: _('Normal'), value: 4 },
    { text: _('High'), value: 7 },
];

const MIN_CELL_PX = 4;
const CELL_GAP_PX = 1;
// A torrent can have anywhere from a handful of pieces to tens of
// thousands. Rather than one square per piece (unreadable, and slow to
// draw, once there are thousands of them), rows are capped to this pixel
// budget -- computeLayout() groups multiple pieces per square as needed
// to stay under it, so the view stays a compact overview no matter how
// many pieces the torrent has.
const MAX_CANVAS_HEIGHT_PX = 360;

Deluge.piecepriority.PiecePriorityTab = Ext.extend(Ext.Panel, {
    title: _('Pieces'),
    autoScroll: true,
    bodyStyle: 'padding: 8px 10px;',

    constructor: function () {
        Deluge.piecepriority.PiecePriorityTab.superclass.constructor.call(
            this
        );

        this.curTorrent = null;
        this.selected = {};
        this.numPieces = 0;
        this.renderPieces = null;
        this.priorities = null;
        this.layout = null;
        this.hoverCell = null;
        this.config = {
            not_dled_color: '#8A8A8A',
            unavailable_color: '#FF00FF',
            dled_color: '#FF0000',
            dling_color: '#0000FF',
            urgent_color: '#FFA500',
            skipped_color: '#000000',
            selected_border: '#FFFFFF',
            hover_border: '#5a5a5a',
            square_size: 10,
            square_border_size: 2,
        };

        this.legendEl = this.add({
            xtype: 'box',
            autoEl: { tag: 'div', cls: 'piecepriority-legend' },
        });
        this.canvasBox = this.add({
            xtype: 'box',
            autoEl: {
                tag: 'canvas',
                id: 'piecepriority_canvas',
                cls: 'piecepriority-canvas',
            },
            listeners: {
                render: function (box) {
                    this.canvasEl = box.getEl().dom;
                    this.ctx = this.canvasEl.getContext('2d');
                    box.getEl().on('mousedown', this.onCanvasMouseDown, this);
                    box.getEl().on(
                        'contextmenu',
                        this.onCanvasContextMenu,
                        this
                    );
                    box.getEl().on('mousemove', this.onCanvasMouseMove, this);
                    box.getEl().on(
                        'mouseleave',
                        this.onCanvasMouseLeave,
                        this
                    );
                },
                scope: this,
            },
        });
        this.infoEl = this.add({
            xtype: 'box',
            autoEl: { tag: 'div', cls: 'piecepriority-info' },
        });

        this.tooltipEl = Ext.DomHelper.append(
            document.body,
            { tag: 'div', cls: 'piecepriority-tooltip' },
            true
        );
        this.tooltipEl.setStyle('display', 'none');

        this.buildLegend();
        this.on('resize', this.onPanelResize, this);
        this.on('destroy', this.onTabDestroy, this);
    },

    onTabDestroy: function () {
        if (this.tooltipEl) {
            this.tooltipEl.remove();
            this.tooltipEl = null;
        }
    },

    loadConfig: function () {
        if (this.configLoaded) return;
        this.configLoaded = true;
        deluge.client.piecepriority.get_config({
            success: function (config) {
                Ext.apply(this.config, config);
                this.buildLegend();
                this.layout = null;
                if (this.curTorrent) this.update(this.curTorrent);
            },
            scope: this,
        });
    },

    /* --- legend / info --- */

    buildLegend: function () {
        if (!this.legendEl.getEl()) return;
        // Ordered as the piece lifecycle, dead to done: skipped pieces
        // never advance, unavailable ones can't advance until a peer
        // that has them shows up, missing ones are wanted but idle,
        // urgent ones are wanted next, then in-flight, then complete.
        const items = [
            { color: this.config.skipped_color, label: _('Skipped') },
            { color: this.config.unavailable_color, label: _('Unavailable') },
            { color: this.config.not_dled_color, label: _('Missing') },
            { color: this.config.urgent_color, label: _('Urgent') },
            { color: this.config.dling_color, label: _('Downloading') },
            { color: this.config.dled_color, label: _('Have') },
        ];
        const html = items
            .map(function (item) {
                return (
                    '<span class="piecepriority-legend-item">' +
                    '<span class="piecepriority-legend-swatch" style="background:' +
                    item.color +
                    ';"></span>' +
                    Ext.util.Format.htmlEncode(item.label) +
                    '</span>'
                );
            })
            .join('');
        this.legendEl.getEl().dom.innerHTML = html;
    },

    updateInfo: function () {
        if (!this.infoEl.getEl()) return;
        const size =
            typeof fsize === 'function'
                ? fsize(this.pieceLength || 0)
                : (this.pieceLength || 0) + ' B';
        let text =
            this.numPieces +
            ' ' +
            _('pieces') +
            ' × ' +
            size +
            ' ' +
            _('each');
        if (this.layout && this.layout.piecesPerCell > 1) {
            text +=
                ' — ' +
                _('each square represents') +
                ' ' +
                this.layout.piecesPerCell +
                ' ' +
                _('pieces');
        }
        this.infoEl.getEl().dom.innerHTML = Ext.util.Format.htmlEncode(text);
    },

    /* --- layout: adapts square size / grouping to piece count and width --- */

    computeLayout: function (numPieces, width) {
        const cellSize = Math.max(MIN_CELL_PX, this.config.square_size);
        const gap = CELL_GAP_PX;
        let piecesPerCell = 1;
        let cols, rows, cells;
        for (;;) {
            cells = Math.ceil(numPieces / piecesPerCell);
            cols = Math.max(
                1,
                Math.min(cells, Math.floor((width + gap) / (cellSize + gap)))
            );
            rows = Math.ceil(cells / cols);
            if (
                rows * (cellSize + gap) <= MAX_CANVAS_HEIGHT_PX ||
                piecesPerCell >= numPieces
            ) {
                break;
            }
            piecesPerCell *= 2;
        }
        return { cellSize, gap, piecesPerCell, cols, rows, cells };
    },

    relayout: function () {
        // The canvas's own rendered width is circular to depend on here --
        // draw() is what sets it, from a previous layout -- and an
        // unstyled canvas has a fixed intrinsic size that ignores its
        // container anyway. Its parent box (the Ext-generated wrapper div
        // around this component) reports the real available width.
        const parent = this.canvasEl && this.canvasEl.parentElement;
        const rawWidth = parent
            ? parent.getBoundingClientRect().width - 4
            : 300;
        this.layout = this.computeLayout(this.numPieces, Math.max(50, rawWidth));
    },

    /* --- selection / hover hit-testing --- */

    canvasXY: function (e) {
        const be = e.browserEvent || e;
        const rect = this.canvasEl.getBoundingClientRect();
        return { x: be.clientX - rect.left, y: be.clientY - rect.top };
    },

    cellIndexFromXY: function (x, y) {
        if (!this.layout) return null;
        const { cellSize, gap, cols, cells } = this.layout;
        const col = Math.floor(x / (cellSize + gap));
        const row = Math.floor(y / (cellSize + gap));
        if (col < 0 || col >= cols || row < 0) return null;
        if (x - col * (cellSize + gap) >= cellSize) return null;
        if (y - row * (cellSize + gap) >= cellSize) return null;
        const idx = row * cols + col;
        return idx < cells ? idx : null;
    },

    onCanvasMouseDown: function (e) {
        const xy = this.canvasXY(e);
        const cell = this.cellIndexFromXY(xy.x, xy.y);
        if (cell === null) return;
        const be = e.browserEvent || e;

        if (!be.ctrlKey && !be.shiftKey) this.selected = {};
        if (be.shiftKey && this.lastClicked !== undefined) {
            const lo = Math.min(this.lastClicked, cell);
            const hi = Math.max(this.lastClicked, cell);
            for (let i = lo; i <= hi; i++) this.selected[i] = true;
        } else {
            this.selected[cell] = !be.ctrlKey || !this.selected[cell];
            if (!this.selected[cell]) delete this.selected[cell];
        }
        this.lastClicked = cell;
        this.dragging = true;
        this.dragValue = !!this.selected[cell];
        this.draw();

        const onUp = function () {
            this.dragging = false;
            Ext.getDoc().un('mouseup', onUp, this);
        };
        Ext.getDoc().on('mouseup', onUp, this);
    },

    onCanvasMouseMove: function (e) {
        const xy = this.canvasXY(e);
        const cell = this.cellIndexFromXY(xy.x, xy.y);

        if (this.dragging) {
            if (cell !== null) {
                if (this.dragValue) this.selected[cell] = true;
                else delete this.selected[cell];
            }
        }

        if (cell !== this.hoverCell) {
            this.hoverCell = cell;
            this.updateTooltip(e, cell);
            this.draw();
        } else if (cell !== null) {
            this.positionTooltip(e);
        }
    },

    onCanvasMouseLeave: function () {
        this.hoverCell = null;
        this.hideTooltip();
        this.draw();
    },

    /* --- tooltip --- */

    updateTooltip: function (e, cell) {
        if (cell === null || !this.layout) {
            this.hideTooltip();
            return;
        }
        const { piecesPerCell } = this.layout;
        const start = cell * piecesPerCell;
        const end = Math.min(this.numPieces, start + piecesPerCell) - 1;
        let label =
            piecesPerCell > 1
                ? _('Pieces') + ' ' + start + '–' + end
                : _('Piece') + ' ' + start;
        if (this.priorities && this.priorities.length === this.numPieces) {
            const prios = this.priorities.slice(start, end + 1);
            if (prios.length) {
                const lo = Math.min.apply(null, prios);
                const hi = Math.max.apply(null, prios);
                label +=
                    ' — ' +
                    _('priority') +
                    ' ' +
                    (lo === hi ? lo : lo + '–' + hi);
            }
        }
        if (this.renderPieces && this.renderPieces.length > end) {
            let unavailable = 0;
            for (let p = start; p <= end; p++) {
                if (this.renderPieces[p] === PIECE_STATE_UNAVAILABLE) unavailable++;
            }
            if (unavailable) {
                label += ' — ' + unavailable + ' ' + _('unavailable in swarm');
            }
        }
        this.tooltipEl.dom.innerHTML = Ext.util.Format.htmlEncode(label);
        this.tooltipEl.setStyle('display', 'block');
        this.positionTooltip(e);
    },

    positionTooltip: function (e) {
        const be = e.browserEvent || e;
        this.tooltipEl.setLeft(be.pageX + 12);
        this.tooltipEl.setTop(be.pageY + 12);
    },

    hideTooltip: function () {
        if (this.tooltipEl) this.tooltipEl.setStyle('display', 'none');
    },

    /* --- priority menu --- */

    onCanvasContextMenu: function (e) {
        e.preventDefault();
        const xy = this.canvasXY(e);
        const cell = this.cellIndexFromXY(xy.x, xy.y);
        if (cell === null) return;
        if (!this.selected[cell]) {
            this.selected = {};
            this.selected[cell] = true;
            this.draw();
        }

        if (!this.priorityMenu) {
            this.priorityMenu = new Ext.menu.Menu({
                items: PRIORITY_OPTIONS.map(
                    function (opt) {
                        return {
                            text: opt.text,
                            handler: this.onPrioritySelected.createDelegate(
                                this,
                                [opt.value]
                            ),
                        };
                    }.bind(this)
                ),
            });
        }
        this.priorityMenu.showAt(e.getXY());
    },

    onPrioritySelected: function (priority) {
        if (!this.curTorrent || !this.layout) return;
        const { piecesPerCell } = this.layout;
        const priorities = {};
        for (const cellStr in this.selected) {
            if (!this.selected[cellStr]) continue;
            const cell = parseInt(cellStr, 10);
            const start = cell * piecesPerCell;
            const end = Math.min(this.numPieces, start + piecesPerCell);
            for (let p = start; p < end; p++) priorities[p] = priority;
        }
        if (Ext.isEmpty(priorities)) return;
        deluge.client.piecepriority.set_piece_priorities(
            this.curTorrent,
            priorities,
            { scope: this }
        );
    },

    /* --- piece grid rendering --- */

    // Download state wins: priority only matters for bytes that aren't
    // moving yet. Next, swarm availability: a wanted missing piece no
    // connected peer has (state 0) colors its cell unavailable — only
    // new peers can fix that cell, which is what makes it worth showing
    // over the priority shades (skipped pieces' unavailability is noise
    // and doesn't trigger it). Remaining purely-missing cells take the
    // *maximum* priority among their missing pieces (a binned cell
    // holding even one urgent piece pops as urgent; it reads skipped
    // only when every missing piece in it is skipped). Mirrors
    // piece_layout.py's color_for_cell.
    colorForCell: function (cellIndex) {
        const { piecesPerCell } = this.layout;
        const start = cellIndex * piecesPerCell;
        const end = Math.min(this.numPieces, start + piecesPerCell);
        const prios =
            this.priorities && this.priorities.length === this.numPieces
                ? this.priorities
                : null;
        let allHave = true;
        let anyDownloading = false;
        let anyUnavailableWanted = false;
        let maxMissingPriority = null;
        for (let p = start; p < end; p++) {
            const state = this.renderPieces[p];
            if (state !== PIECE_STATE_HAVE) {
                allHave = false;
                const prio = prios ? prios[p] : null;
                if (prio !== null && (maxMissingPriority === null || prio > maxMissingPriority)) {
                    maxMissingPriority = prio;
                }
                if (
                    state === PIECE_STATE_UNAVAILABLE &&
                    (prio === null || prio > PIECE_PRIORITY_SKIP)
                ) {
                    anyUnavailableWanted = true;
                }
            }
            if (state === PIECE_STATE_DOWNLOADING) anyDownloading = true;
        }
        if (allHave) return this.config.dled_color;
        if (anyDownloading) return this.config.dling_color;
        if (anyUnavailableWanted) return this.config.unavailable_color;
        if (maxMissingPriority !== null) {
            if (maxMissingPriority >= PIECE_PRIORITY_URGENT)
                return this.config.urgent_color;
            if (maxMissingPriority === PIECE_PRIORITY_SKIP)
                return this.config.skipped_color;
        }
        return this.config.not_dled_color;
    },

    draw: function () {
        if (!this.ctx || !this.layout || !this.renderPieces) return;
        const { cellSize, gap, cols, rows, cells } = this.layout;
        this.canvasEl.width = cols * (cellSize + gap) - gap;
        this.canvasEl.height = rows * (cellSize + gap) - gap;
        // Pin the CSS box to the bitmap's own size (rather than, say, a
        // stretch-to-100%-width rule) so squares stay crisp instead of
        // being scaled by the browser.
        this.canvasEl.style.width = this.canvasEl.width + 'px';
        this.canvasEl.style.height = this.canvasEl.height + 'px';
        const ctx = this.ctx;
        ctx.clearRect(0, 0, this.canvasEl.width, this.canvasEl.height);

        const borderLw = Math.max(
            1,
            Math.min(this.config.square_border_size, Math.floor(cellSize / 2))
        );

        for (let c = 0; c < cells; c++) {
            const col = c % cols;
            const row = Math.floor(c / cols);
            const x = col * (cellSize + gap);
            const y = row * (cellSize + gap);

            ctx.fillStyle = this.colorForCell(c);
            ctx.fillRect(x, y, cellSize, cellSize);

            if (this.selected[c]) {
                ctx.strokeStyle = this.config.selected_border;
                ctx.lineWidth = borderLw;
                ctx.strokeRect(
                    x + borderLw / 2,
                    y + borderLw / 2,
                    cellSize - borderLw,
                    cellSize - borderLw
                );
            } else if (c === this.hoverCell) {
                ctx.strokeStyle = this.config.hover_border;
                ctx.lineWidth = 1;
                ctx.strokeRect(x + 0.5, y + 0.5, cellSize - 1, cellSize - 1);
            }
        }
    },

    onStatus: function (status) {
        // A finished torrent's 'pieces' field is null -- Deluge's own
        // get_torrent_status stops tracking per-piece download state once
        // there's nothing left to download -- so treat that as "every
        // piece has" rather than indexing into null.
        this.renderPieces =
            status.pieces ||
            new Array(status.num_pieces).fill(PIECE_STATE_HAVE);
        this.pieceLength = status.piece_length;

        if (status.num_pieces !== this.numPieces || !this.layout) {
            this.numPieces = status.num_pieces;
            this.selected = {};
            this.relayout();
        }
        this.updateInfo();
        this.draw();
    },

    update: function (torrentId) {
        this.loadConfig();
        if (torrentId !== this.curTorrent) {
            this.curTorrent = torrentId;
            this.selected = {};
            this.layout = null;
        }
        deluge.client.core.get_torrent_status(
            torrentId,
            ['pieces', 'num_pieces', 'piece_length'],
            { success: this.onStatus, scope: this }
        );
        // Priorities render as an overlay on missing pieces; fetched on
        // the same cadence so the view tracks scheduling (a streaming
        // client's moving deadline window, a manual skip) live. Failure
        // (metadata still resolving) just means no overlay this tick.
        deluge.client.piecepriority.get_piece_priorities(torrentId, {
            success: this.onPriorities,
            failure: function () {
                this.onPriorities(null);
            },
            scope: this,
        });
    },

    onPriorities: function (priorities) {
        this.priorities = priorities;
        this.draw();
    },

    onPanelResize: function () {
        if (!this.renderPieces) return;
        this.relayout();
        this.draw();
    },

    clear: function () {
        this.curTorrent = null;
        this.numPieces = 0;
        this.renderPieces = null;
        this.priorities = null;
        this.layout = null;
        this.selected = {};
        this.hoverCell = null;
        this.hideTooltip();
        if (this.ctx && this.canvasEl) {
            this.ctx.clearRect(0, 0, this.canvasEl.width, this.canvasEl.height);
        }
        if (this.infoEl.getEl()) this.infoEl.getEl().dom.innerHTML = '';
    },
});

Deluge.piecepriority.PiecePriorityPlugin = Ext.extend(Deluge.Plugin, {
    name: 'PiecePriority',

    onDisable: function () {
        deluge.details.remove(this.tab);
    },

    onEnable: function () {
        this.tab = new Deluge.piecepriority.PiecePriorityTab();
        deluge.details.add(this.tab);
    },
});

Deluge.registerPlugin(
    'PiecePriority',
    Deluge.piecepriority.PiecePriorityPlugin
);
