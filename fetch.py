#!/usr/bin/env python3

import csv
import io

import requests

import db

FUND_LIBRARY_URL = (
    "https://toushin-lib.fwg.ne.jp/"
    "FdsWeb/FDST030000/csv-file-download"
)


def parse_date(value):
    value = value.strip()

    if "年" in value:
        value = (
            value
            .replace("年", "-")
            .replace("月", "-")
            .replace("日", "")
        )

    value = value.replace("/", "-")

    year, month, day = value.split("-")
    return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"


def get_nav(isin, code, name):
    response = requests.get(
        FUND_LIBRARY_URL,
        params={
            "isinCd": isin,
            "associFundCd": code,
        },
        timeout=60,
    )
    response.raise_for_status()

    text = response.content.decode("cp932")

    if text.strip() == '{"statusCode":null}':
        raise RuntimeError(f"Fund not found: {isin}")

    rows = list(csv.reader(io.StringIO(text)))

    header_index = next(
        i
        for i, row in enumerate(rows)
        if "基準価額(円)" in row
    )

    header = rows[header_index]

    date_index = next(
        i
        for i, value in enumerate(header)
        if value in ("年月日", "基準日", "日付")
    )

    price_index = header.index("基準価額(円)")

    records = []

    for row in rows[header_index + 1:]:
        if len(row) <= max(date_index, price_index):
            continue

        date = row[date_index].strip()
        price = row[price_index].strip().replace(",", "")

        if not date or not price:
            continue

        try:
            price = float(price)
        except ValueError:
            continue

        records.append(
            {
                "isin": isin,
                "code": code,
                "name": name,
                "date": parse_date(date),
                "nav": price,
            }
        )

    return records


def main():
    records = []
    new = 0

    for isin, fund in db.FUNDS.items():
        name = fund["name"]
        code = fund["code"]

        print("=" * 70)
        print(f"Downloading: {name}")
        print(f"Fund code : {code}")
        print(f"ISIN      : {isin}")

        nav = get_nav(isin, code, name)
        print(f"Records   : {len(nav):,}")
        if nav:
            print(f"First     : {nav[0]['date']} {nav[0]['nav']}")
            print(f"Last      : {nav[-1]['date']} {nav[-1]['nav']}")

        records.extend(nav)

    db.update_prices(
        [{"isin": r["isin"], "date": r["date"], "nav": r["nav"]}
         for r in records]
    )
    print()

    summary = db.valuation()
    print("=" * 70)
    print("Portfolio")
    print(f"Total invested : ¥{summary['total_cost']:,.0f}")
    print(f"Current value  : ¥{summary['total_value']:,.0f}")
    print(f"Profit         : ¥{summary['total_profit']:+,.0f} "
          f"({summary['total_profit_pct']:+.1f}%)")
    print(f"As of          : {summary['last_price_date']}")


if __name__ == "__main__":
    main()