"""Offline-Tests der Parser. Alle Werte sind erfunden (ISINs mit ZZ), die Formate entsprechen den Originalen."""
import json
import unittest
from datetime import datetime, timezone

from marktdaten import synth
from marktdaten.buch import Orderbuch
from marktdaten.modell import KAUF, VERKAUF, BuchSeite, BuchStufe, Quote, Trade, iso_aus_ns, ns_aus_iso, zahl
from marktdaten.quellen import BOEAG, CBOE, EQUIDUCT, EURONEXT, GETTEX, NGM, NORDIC, TRADEGATE, XETRA

START = datetime(2026, 1, 5, 9, 0, tzinfo=timezone.utc)


class Modell(unittest.TestCase):
    def test_zeit(self):
        ns = ns_aus_iso("2026-10-20T15:22:59.998840347Z")
        self.assertEqual(iso_aus_ns(ns), "2026-10-20T15:22:59.998840347Z")
        self.assertEqual(ns_aus_iso("2026-10-20 07:00:01.607663"), ns_aus_iso("2026-10-20T07:00:01.607663000Z"))

    def test_zahl(self):
        self.assertEqual(zahl("10,6000"), 10.6)
        self.assertEqual(zahl("1.234,5"), 1234.5)
        self.assertEqual(zahl("42.5"), 42.5)
        self.assertIsNone(zahl(""))
        self.assertIsNone(zahl("-"))


class DeutscheBoerse(unittest.TestCase):
    def test_synth_xetra(self):
        pre, post = synth.xetra(START, 1)
        ereignisse = [e for z in pre for e in XETRA.umwandeln(json.loads(z))]
        seiten = [e for e in ereignisse if isinstance(e, BuchSeite)]
        self.assertTrue(seiten and all(len(e.stufen) == 5 for e in seiten))
        self.assertTrue(any(isinstance(e, Quote) for e in ereignisse))
        trades = [e for z in post for e in XETRA.umwandeln(json.loads(z))]
        self.assertTrue(trades and all(isinstance(t, Trade) and t.venue == "XETR" for t in trades))

    def test_retail_und_teilupdate(self):
        d = {"messageId": "pretrade", "instrumentIdentificationCode": "ZZ0000000011", "priceCurrency": "EUR",
             "updateDateAndTime": "2026-01-05T09:00:00.000000001Z", "retailFlag": True,
             "aggregatedNumberOfOrdersAndQuotesBid": [{"noOfOrders": 2}],
             "mdBidMktDepthGroup1": [{"price": 10.0, "quantity": 100.0}]}
        (e,) = XETRA.umwandeln(d)
        self.assertEqual((e.buch, e.seite, e.stufen), ("retail", KAUF, [(10.0, 100.0, 2)]))

    def test_tradegate_feldnamen(self):
        d = {"messageId": "pretrade", "instrumentIdentificationCode": "ZZ0000000011", "tradingSystem": "7",
             "updateDateAndTime": "2026-01-05T09:00:00.009000000Z", "ask": 11.0, "bid": 10.9,
             "bidQty": 150.0, "askQty": 100.0, "priceCurrency": "EUR", "venueOfExecution": "XGAT"}
        (q,) = TRADEGATE.umwandeln(d)
        self.assertEqual((q.bid, q.ask, q.bid_menge, q.venue), (10.9, 11.0, 150.0, "XGAT"))


class Cboe(unittest.TestCase):
    def test_synth_und_buch(self):
        pre, post = synth.cboe(START, 1, segment="BATE")
        stufen = list(CBOE.umwandeln(pre, "pretrade"))
        self.assertTrue(stufen and all(isinstance(e, BuchStufe) for e in stufen))
        buch = Orderbuch()
        for e in stufen:
            k = buch.anwenden(e)
        self.assertIsNotNone(buch.spread_bp(k))
        self.assertTrue(all(isinstance(t, Trade) for t in CBOE.umwandeln(post, "posttrade")))

    def test_invalidierung_und_si(self):
        zeilen = ["Update Date and Time,Instrument...",            # Kopfzeile der ersten Tagesdatei
                  "2026-01-05 09:00:00.000001,ZZ0000000011,MUSTd,SELL,121.0000,EUR,MONE,1500,EUR,5,CEUX,CLOB,COTR,2026-01-05 09:00:00.000002",
                  "2026-01-05 09:00:01.000001,ZZ0000000011,MUSTd,SELL,121.0000,EUR,MONE,0,EUR,0,CEUX,CLOB,COTR,2026-01-05 09:00:01.000002",
                  "2026-01-05 09:00:02.000001,ZZ0000000011,MUSTd,BUYL,120.0000,EUR,5000,ZSIX,2026-01-05 09:00:02.000002"]
        e = list(CBOE.umwandeln(zeilen, "pretrade"))
        buch = Orderbuch()
        k = buch.anwenden(e[0]); buch.anwenden(e[1])
        self.assertEqual(buch.stufen(k, VERKAUF), [])
        self.assertTrue(isinstance(e[2], Quote) and e[2].extra["si"] and e[2].bid == 120.0)


class WeitereFormate(unittest.TestCase):
    def test_euronext(self):
        zeilen = ["(c) 2026 Euronext N.V. All Rights Reserved.",
                  "TradingDateTime,PublicationDateTime,MifidInstrumentID,MifidPrice,MifidQuantity,MifidPriceNotation,"
                  "MifidCurrency,MmtMarketMechanism,MmtNegotiationIndicator,MmtModificationIndicator,MmtBenchMarkIndicator,"
                  "MmtContributionToPrice,MmtAlgoIndicator,MmtPublicationMode,Venue,ThirdCountry,TradeUniqueIdentifier,"
                  "MissingPrice,ContingentTx,VenueOfPublication",
                  '"2026-01-05T09:00:00.000000Z","2026-01-05T09:00:00.100000Z","ZZ0000000011","42.5000000","280.0",'
                  '"MONE","EUR","1","-","-","-","-","ALGO","-","XPAR","","SYN1","","-","XPAR"']
        (t,) = EURONEXT.umwandeln(zeilen)
        self.assertEqual((t.preis, t.menge, t.venue, t.extra.get("mmtalgoindicator")), (42.5, 280.0, "XPAR", "ALGO"))

    def test_nordic(self):
        zeilen = ['"sep=;"',
                  "Update date and time;Instrument identification code;Side;Market Marker;Price;Price currency;Price notation;"
                  "Quantity;Quantity currency;Aggregated number of orders and quotes;Venue;Trading system;Trading system phase;"
                  "Publication date and time",
                  "2026-01-05T09:00:00.186000Z;ZZ0000000011;SELL;;8.63;EUR;1;9500.0;EUR;1.0;XHEL;CLOB;COTR;2026-01-05T09:00:00.186000Z"]
        (q,) = NORDIC.umwandeln(zeilen, "pretrade")
        self.assertEqual((q.bid, q.ask, q.ask_menge), (None, 8.63, 9500.0))

    def test_boeag(self):
        pre = ["MIC;ISIN;time;bid;bid_volume;ask;ask_volume;status;",
               "DUSD;ZZ0000000011;2026-01-05T09:00:00.143560Z;10,6000;290;10,9500;290;TRAD;"]
        (q,) = BOEAG.umwandeln(pre, "pretrade")
        self.assertEqual((q.bid, q.ask, q.venue), (10.6, 10.95, "DUSD"))
        post = ["MIC; ISIN; displayName; time; price; size; supplement",
                'DUSA;ZZ0000000011;"Muster AG";05.01.2026 10:00:00;25,5;43;"bez "']
        (t,) = BOEAG.umwandeln(post, "posttrade")
        self.assertEqual(iso_aus_ns(t.ts)[:19], "2026-01-05T09:00:00")    # Winterzeit: UTC+1

    def test_equiduct(self):
        zeilen = ["timestamp,symbol,isin,currency,home_market,tvtic,price,quantity,trade_type,flags",
                  '"20260105-09:00:00.500957","MUSTp","ZZ0000000011","EUR","XPAR","SYN1","207.0","2","-",="-H"']
        (t,) = EQUIDUCT.umwandeln(zeilen, "posttrade")
        self.assertEqual((t.preis, t.extra["flags"]), (207.0, "-H"))

    def test_ngm(self):
        zeilen = ["ISIN,Symbol,Currency,Bid price, Bid quantity, Ask price, Ask quantity",
                  "ZZ0000000011,MUSTER,SEK,,,,", "ZZ0000000029,BEISPIEL,NOK,16.500,3000,16.660,3000"]
        (q,) = NGM.umwandeln(zeilen, "pretrade", "2026-01-05T0900")
        self.assertEqual((q.instrument, q.bid, iso_aus_ns(q.ts)[:16]), ("ZZ0000000029", 16.5, "2026-01-05T09:00"))

    def test_gettex(self):
        datei = "https://erdk.bayerische-boerse.de:8000/delayed-data/MUNC-MUND/pretrade/pretrade.20260105.09.00.munc.csv.gz"
        (q,) = GETTEX.umwandeln(["ZZ0000000011,09:00:02.523397,EUR,66.32,100,66.65,100"], "pretrade", datei)
        self.assertEqual((q.venue, q.bid, iso_aus_ns(q.ts)[:19]), ("MUNC", 66.32, "2026-01-05T09:00:02"))


if __name__ == "__main__":
    unittest.main()
