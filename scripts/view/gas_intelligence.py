from brownie import *
from elasticsearch import Elasticsearch
import numpy as np
import json
import os
from time import time
from dotmap import DotMap

"""
Find the cheapest gas price that could be expected to get mined in a reasonable amount of time.
Historical query returns average gas price for each of the specified 
time units (minutes or hours). 

For API access:
----------------
1. Create a copy of 'credentials.example.json' in the project root 
2. Rename it 'credentials.json'
3. Create an account with https://www.anyblockanalytics.com/
4. Fill in the values in 'credentials.json'

Entry Point: 
----------------
analyze_gas()

Returns:
----------------
- Midpoint of largest bin from gas price histogram (this is the expected best gas price)
- Standard deviation
- Average gas price

Test:
----------------
test()
"""

HISTORICAL_URL = "https://api.anyblock.tools/ethereum/ethereum/mainnet/es/"
CREDENTIALS = "/../../any-block-credentials.json"
BINS = 60  # number of bins for histogram

# convert wei to gwei
def to_gwei(x: float) -> float:
    return x / 10 ** 9


# Initialize the ElasticSearch Client
def initialize_elastic(network: str) -> any:
    # Api access credentials from https://www.anyblockanalytics.com/
    AUTH = json.load(open(os.path.dirname(__file__) + CREDENTIALS))
    return Elasticsearch(
        hosts=[network], http_auth=(AUTH["email"], AUTH["key"]), timeout=180
    )


# fetch average hourly gas prices over the last specified hours
def fetch_gas_hour(network: str, hours=24) -> list[float]:
    es = initialize_elastic(network)
    now = int(time())
    seconds = hours * 3600
    data = es.search(
        index="tx",
        doc_type="tx",
        body={
            "_source": ["timestamp", "gasPrice.num"],
            "query": {
                "bool": {
                    "must": [
                        {"range": {"timestamp": {"gte": now - seconds, "lte": now}}}
                    ]
                }
            },
            "aggs": {
                "hour_bucket": {
                    "date_histogram": {
                        "field": "timestamp",
                        "interval": "1H",
                        "format": "yyyy-MM-dd hh:mm:ss",
                    },
                    "aggs": {"avgGasHour": {"avg": {"field": "gasPrice.num"}}},
                }
            },
        },
    )
    return [
        x["avgGasHour"]["value"]
        for x in data["aggregations"]["hour_bucket"]["buckets"]
        if x["avgGasHour"]["value"]
    ]


# fetch average gas prices per minute over the last specified minutes
def fetch_gas_min(network: str, minutes=60) -> list[float]:
    es = initialize_elastic(network)
    now = int(time())
    seconds = minutes * 60
    data = es.search(
        index="tx",
        doc_type="tx",
        body={
            "_source": ["timestamp", "gasPrice.num"],
            "query": {
                "bool": {
                    "must": [
                        {"range": {"timestamp": {"gte": now - seconds, "lte": now}}}
                    ]
                }
            },
            "aggs": {
                "minute_bucket": {
                    "date_histogram": {
                        "field": "timestamp",
                        "interval": "1m",
                        "format": "yyyy-MM-dd hh:mm",
                    },
                    "aggs": {"avgGasMin": {"avg": {"field": "gasPrice.num"}}},
                }
            },
        },
    )
    return [
        x["avgGasMin"]["value"]
        for x in data["aggregations"]["minute_bucket"]["buckets"]
        if x["avgGasMin"]["value"]
    ]


def is_outlier(points: list[float], thresh=3.5) -> list[bool]:
    """
    Returns a boolean array with True if points are outliers and False
    otherwise.

    Parameters:
    -----------
        points : A numobservations by numdimensions array of observations
        thresh : The modified z-score to use as a threshold. Observations with
            a modified z-score (based on the median absolute deviation) greater
            than this value will be classified as outliers.

    Returns:
    --------
        mask : A numobservations-length boolean array.

    References:
    ----------
        Boris Iglewicz and David Hoaglin (1993), "Volume 16: How to Detect and
        Handle Outliers", The ASQC Basic References in Quality Control:
        Statistical Techniques, Edward F. Mykytka, Ph.D., Editor.
    """
    if len(points.shape) == 1:
        points = points[:, None]
    median = np.median(points, axis=0)
    diff = np.sum((points - median) ** 2, axis=-1)
    diff = np.sqrt(diff)
    med_abs_deviation = np.median(diff)

    modified_z_score = 0.6745 * diff / med_abs_deviation

    return modified_z_score > thresh


# main entry point
def analyze_gas(
    options={"timeframe": "minutes", "periods": 60}
) -> tuple[int, int, int]:
    if not os.path.isfile(os.path.dirname(__file__) + CREDENTIALS):
        print("Could not fetch historical gas data")
        return DotMap(
            mode=999999999999999999, median=999999999999999999, std=999999999999999999
        )

    gas_data = []

    # fetch data
    if options["timeframe"] == "minutes":
        gas_data = fetch_gas_min(HISTORICAL_URL, options["periods"])
    else:
        gas_data = fetch_gas_hour(HISTORICAL_URL, options["periods"])
    gas_data = np.array(gas_data)

    # remove outliers
    filtered_gas_data = gas_data[~is_outlier(gas_data)]

    # Create histogram
    counts, bins = np.histogram(filtered_gas_data, bins=BINS)

    # Find most common gas price
    biggest_bin = 0
    biggest_index = 0
    for i, x in enumerate(counts):
        if x > biggest_bin:
            biggest_bin = x
            biggest_index = i
    midpoint = (bins[biggest_index] + bins[biggest_index + 1]) / 2

    if int(midpoint) == 0:
        print("Could not fetch historical gas data")
        return DotMap(
            mode=999999999999999999, median=999999999999999999, std=999999999999999999
        )

    # standard deviation
    standard_dev = np.std(filtered_gas_data, dtype=np.float64)

    # average
    median = np.median(filtered_gas_data, axis=0)

    return DotMap(mode=int(midpoint), median=int(median), std=int(standard_dev))


# run this to test analyze_gas and print values
def main() -> tuple[int, int, int]:
    results = analyze_gas()
    print("timeframe:", "minutes")
    print("approximate most common gas price:", to_gwei(results["mode"]))
    print("average gas price:", to_gwei(results["median"]))
    print("standard deviation:", to_gwei(results["std"]))
    return results
