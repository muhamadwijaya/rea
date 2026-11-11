// TradingView embed widgets (official, free). They stream realtime data from TradingView directly.

const BASE = 'https://s3.tradingview.com/external-embedding/embed-widget-';
const LOCALE = 'id';

export function mountWidget(container, name, config) {
  if (!container) return;
  container.innerHTML = '';
  const wrap = document.createElement('div');
  wrap.className = 'tradingview-widget-container';
  wrap.style.cssText = 'height:100%;width:100%';
  const inner = document.createElement('div');
  inner.className = 'tradingview-widget-container__widget';
  inner.style.cssText = 'height:100%;width:100%';
  wrap.appendChild(inner);
  const script = document.createElement('script');
  script.type = 'text/javascript';
  script.async = true;
  script.src = `${BASE}${name}.js`;
  script.innerHTML = JSON.stringify({ locale: LOCALE, colorTheme: 'dark', isTransparent: true, ...config });
  wrap.appendChild(script);
  container.appendChild(wrap);
}

export const TV_INTERVAL = { '15m': '15', '1h': '60', '4h': '240', '1d': 'D', '1w': 'W' };

export function tickerTape(el) {
  mountWidget(el, 'ticker-tape', {
    symbols: [
      { proName: 'FX:EURUSD', title: 'EUR/USD' },
      { proName: 'FX:GBPUSD', title: 'GBP/USD' },
      { proName: 'FX:USDJPY', title: 'USD/JPY' },
      { proName: 'OANDA:XAUUSD', title: 'GOLD' },
      { proName: 'TVC:DXY', title: 'DXY' },
      { proName: 'SP:SPX', title: 'S&P 500' },
      { proName: 'NASDAQ:NDX', title: 'NASDAQ 100' },
      { proName: 'IDX:COMPOSITE', title: 'IHSG' },
      { proName: 'FX_IDC:USDIDR', title: 'USD/IDR' },
      { proName: 'TVC:USOIL', title: 'WTI' },
      { proName: 'BITSTAMP:BTCUSD', title: 'BTC' },
      { proName: 'IDX:BBCA', title: 'BBCA' },
      { proName: 'NASDAQ:NVDA', title: 'NVDA' },
    ],
    showSymbolLogo: true,
    displayMode: 'adaptive',
  });
}

export function advancedChart(el, tvSymbol, tf) {
  mountWidget(el, 'advanced-chart', {
    autosize: true,
    symbol: tvSymbol,
    interval: TV_INTERVAL[tf] || '60',
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || 'Asia/Jakarta',
    theme: 'dark',
    style: '1',
    isTransparent: false,
    backgroundColor: 'rgba(10, 14, 22, 1)',
    gridColor: 'rgba(28, 38, 55, 0.6)',
    allow_symbol_change: true,
    hide_side_toolbar: false,
    withdateranges: true,
    details: true,
    calendar: false,
    studies: ['STD;EMA', 'STD;RSI'],
    support_host: 'https://www.tradingview.com',
  });
}

export function technicalGauge(el, tvSymbol, tf) {
  mountWidget(el, 'technical-analysis', {
    interval: tf === '15m' ? '15m' : tf === '4h' ? '4h' : tf === '1d' ? '1D' : tf === '1w' ? '1W' : '1h',
    width: '100%',
    height: '100%',
    symbol: tvSymbol,
    showIntervalTabs: true,
    displayMode: 'single',
  });
}

export function screenerWidget(el, market) {
  mountWidget(el, 'screener', {
    width: '100%',
    height: '100%',
    defaultColumn: 'overview',
    defaultScreen: market === 'forex' ? 'general' : 'most_capitalized',
    market,
    showToolbar: true,
  });
}

export function timeline(el, market) {
  mountWidget(el, 'timeline', { feedMode: 'market', market, displayMode: 'regular', width: '100%', height: '100%' });
}

export function economicCalendar(el) {
  mountWidget(el, 'events', {
    width: '100%',
    height: '100%',
    importanceFilter: '0,1',
    countryFilter: 'us,eu,gb,jp,au,ca,ch,nz,cn,id',
  });
}

export function heatmap(el, kind) {
  const currencies = ['EUR', 'USD', 'JPY', 'GBP', 'CHF', 'AUD', 'CAD', 'NZD'];
  if (kind === 'cross') {
    mountWidget(el, 'forex-cross-rates', { width: '100%', height: '100%', currencies });
  } else if (kind === 'stocks') {
    mountWidget(el, 'stock-heatmap', {
      exchanges: [], dataSource: 'SPX500', grouping: 'sector', blockSize: 'market_cap_basic', blockColor: 'change',
      hasTopBar: true, isDataSetEnabled: true, isZoomEnabled: true, hasSymbolTooltip: true, isMonoSize: false,
      width: '100%', height: '100%',
    });
  } else {
    mountWidget(el, 'forex-heat-map', { width: '100%', height: '100%', currencies });
  }
}
