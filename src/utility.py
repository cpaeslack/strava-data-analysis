"""This file contains re-usable utility functions"""

from dotenv import load_dotenv
import datetime
from functools import reduce
from IPython.display import display
import logging
import operator
import os
import pandas as pd
import pickle
import time


class Athlete:
    """Class that represents athlete and activity data"""

    def __init__(self, client):
        self.athlete = client.get_athlete()

    def printBasicAthleteInfo(self):
        logging.info(
            "Athlete's name is {} {}, based in {}, {}".format(
                self.athlete.firstname,
                self.athlete.lastname,
                self.athlete.city,
                self.athlete.country,
            )
        )

    def getAthleteData(self):
        if self.athlete == {}:
            return self.athlete
        else:
            self.athlete["heart_rate_zones"] = self.zones["heart_rate"][
                "zones"
            ]
            return self.athlete

    def getActivities(self):
        return self.activities


def getEnvVariables():
    load_dotenv()
    env_variables = {
        "Strava": {
            "client_id": os.environ.get("STRAVA_CLIENT_ID"),
            "client_secret": os.environ.get("STRAVA_CLIENT_SECRET"),
            "api_url": os.environ.get("STRAVA_API_URL"),
            "athlete_id": os.environ.get("STRAVA_ATHLETE_ID"),
        },
        "Plotly": {
            "theme": os.environ.get("PLOTLY_THEME"),
        },
    }

    return env_variables


def GetAthlete(client):
    athlete = client.get_athlete()
    return athlete


def GetActivities(client, limit=100):
    """
    Assuming there is on average one activity per day.
    The number of activities to show can later still
    be adapted e.g. based on a date.

    We do a conversion to a DataFrame here so data
    can be conveniently accessed.
    """

    activities = client.get_activities(limit=limit)
    assert len(list(activities)) == limit

    data = []
    for activity in activities:
        my_dict = activity.to_dict()
        data.append([activity.id] + [my_dict])

    return data


def processActivityData(activities):
    """
    Select only columns of interest and add activity ID.
    Returns a pandas dataframe.
    """

    my_cols = [
        "name",
        "start_date_local",
        "type",
        "distance",
        "moving_time",
        "elapsed_time",
        "total_elevation_gain",
        "elev_high",
        "elev_low",
        "average_speed",
        "max_speed",
        "average_heartrate",
        "max_heartrate",
        "start_latitude",
        "start_longitude",
        "average_watts",
        "max_watts",
    ]

    data = []
    for activity in activities:
        data.append(activity[1])

    # Add id to the beginning of the columns,
    # used when selecting a specific activity
    my_cols.insert(0, "id")

    df = pd.DataFrame(data, columns=my_cols)

    # Turn virtual rides into normal rides (makes overall
    # bike analysis easier), then name everything 'Bike'
    df["type"] = df["type"].replace("VirtualRide", "Ride")
    df["type"] = df["type"].replace("Ride", "Bike")

    # Create a distance in km column
    df["distance_km"] = df["distance"] / 1e3

    # Convert dates to datetime type
    df["start_date_local"] = pd.to_datetime(df["start_date_local"])

    # Create a day of the week and month of the year columns
    df["day_of_week"] = df["start_date_local"].dt.day_name()
    df["month_of_year"] = df["start_date_local"].dt.month

    # Convert times to minutes
    # df['moving_time'] = pd.to_timedelta(df['moving_time'])
    # df['elapsed_time'] = pd.to_timedelta(df['elapsed_time'])
    df["elapsed_time"] = df["elapsed_time"].astype(int) / 60
    df["moving_time"] = df["moving_time"].astype(int) / 60

    # Convert timings to minutes for plotting
    # df['elapsed_time_hr'] = df['elapsed_time'].astype(int) / 3600e9
    # df['moving_time_hr'] = df['moving_time'].astype(int) / 3600e9
    df["elapsed_time_hr"] = df["elapsed_time"].astype(int) / 60
    df["moving_time_hr"] = df["moving_time"].astype(int) / 60

    df.fillna("", inplace=True)

    return df


def PrintLatestActivity(client, data):
    """
    This will print only the latest activity
    basic info (namne, type, date, duration, mileage,
    avg heart rate, kcal).
    if activity is a bike ride, it also prints average watts.
    """

    activity = client.get_activity(data[0][1]["id"]).to_dict()

    data = {
        "name": activity["name"],
        "type": activity["type"],
        "date": datetime.datetime.fromisoformat(
            activity["start_date"]
        ).strftime("%d.%m.%Y %H:%M:%S Uhr"),
        "duration": str("%.1f" % (activity["moving_time"] / 60)) + " min",
        "mileage": str("%.1f" % (activity["distance"] / 1000)) + " km",
        "avgHR": str("%d" % activity["average_heartrate"]) + " bpm",
        "kcal": str("%d" % activity["calories"]),
    }
    if activity["type"] == "Bike":
        data["average_watts"] = activity["average_watts"]
    df = pd.DataFrame(data, index=["Latest Activity"])
    display(df.T)


def GetStreams(client, activity, types):
    """Returns a Strava 'stream', which is timeseries data from an activity"""

    streams = client.get_activity_streams(
        activity, types=types, series_type="time"
    )
    return streams


def ConvertStream2DataFrame(dict, types):
    """Converts a Stream into a DataFrame and returns it"""

    df = pd.DataFrame()
    for item in types:
        if item in dict.keys():
            df.append(item.data)
    df.fillna("", inplace=True)

    return df


def ParseActivityStream(client, activity, types):
    """Strava 'stream' objects are parsed and converted into
    a Pandas DataFrame object.
    """

    activity_id = activity[1]["id"]
    name = activity[1]["name"]
    streams = GetStreams(client, activity_id, types)
    df = pd.DataFrame()

    # Write each row to a DataFrame
    for item in types:
        if item in streams.keys():
            df[item] = pd.Series(streams[item].data, index=None)
        df["activity_id"] = activity_id
        df["activity_startDate"] = pd.to_datetime(activity[1]["start_date"])
        df["activity_name"] = name

    return df


def getOverallWattsAndCadence(streams):
    """
    First the bike power data is smoothened using moving averages.
    Then it's added as a new field in the stream dictionary.
    Finally, all power and cadence data is accumulated into a single
    list and then converted to a pandas dataframe.

    Returns: bike_watts_total_df, cadence_total_df
    """

    # We take the smoothed values (moving average)
    bike_watts_total = []
    cadence_total = []

    for act in iter(streams):
        if "watts" in (act):
            act["watts_moving_avg"] = (
                act["watts"].rolling(60, min_periods=1).mean()
            )
            bike_watts_total.append(act["watts_moving_avg"].to_list())
            cadence_total.append(act["cadence"].to_list())

    bike_watts_total = reduce(operator.concat, bike_watts_total)
    cadence_total = reduce(operator.concat, cadence_total)
    bike_watts_total_df = pd.DataFrame(bike_watts_total, columns=["power"])
    cadence_total_df = pd.DataFrame(cadence_total, columns=["cadence"])

    return bike_watts_total_df, cadence_total_df


def convertMps2kmh(speed):
    return 3.6 * speed


def setHRzones(maxHeartRate):
    """
    Heart rate training zones based on max Heart Rate (HR max):
    -----------------------------------------------------------
    Zone 1 (recovery/easy)      55%-65% HR max
    Zone 2 (aerobic/base)       65%-75% HR max
    Zone 3 (tempo)              75%-85% HR max
    Zone 4 (lactate threshold)  85%-90% HR max
    Zone 5 (anaerobic)          90% HR max and above
    """

    zones = []
    zones.append([0.55 * maxHeartRate, 0.65 * maxHeartRate])
    zones.append([0.55 * maxHeartRate, 0.75 * maxHeartRate])
    zones.append([0.75 * maxHeartRate, 0.85 * maxHeartRate])
    zones.append([0.85 * maxHeartRate, 0.90 * maxHeartRate])
    zones.append([0.90 * maxHeartRate, 1.00 * maxHeartRate])

    return zones


def setPowerZones(FTP):
    """
    Power training zones based on Functional Threshold Power (FTP):
    ---------------------------------------------------------------
    Z1  Active Recovery   <55%
    Z2  Endurance         56-75%
    Z3  Tempo             76-90%
    Z4  Lactate Threshold 91-105%
    Z5  VO2Max/Anaerobic  >106%
    """

    # zones = []
    # zones.append([0.00 * FTP, 0.55 * FTP])
    # zones.append([0.55 * FTP, 0.75 * FTP])
    # zones.append([0.75 * FTP, 0.90 * FTP])
    # zones.append([0.90 * FTP, 1.05 * FTP])
    # zones.append([1.05 * FTP, 9.00 * FTP])

    zones = [
        0.00 * FTP,
        0.55 * FTP,
        0.75 * FTP,
        0.90 * FTP,
        1.05 * FTP,
        10.00 * FTP,
    ]

    return zones


def readAccessTokenFromFile():
    """Read the access token from the stored binary file"""

    with open("access_token.pickle", "rb") as f:
        access_token = pickle.load(f)
    logging.info("read access token from file.")

    return access_token


def checkAndRefreshToken(client, access_token, client_id, client_secret):
    """Checks if the token has expired and if yes it is refreshed"""

    if time.time() > access_token["expires_at"]:
        logging.info("Token has expired, will refresh")
        refresh_response = client.refresh_access_token(
            client_id=client_id,
            client_secret=client_secret,
            refresh_token=access_token["refresh_token"],
        )
        access_token = refresh_response
        with open("../access_token.pickle", "wb") as f:
            pickle.dump(refresh_response, f)
        logging.info("Refreshed token saved to file")

        client.access_token = refresh_response["access_token"]
        client.refresh_token = refresh_response["refresh_token"]
        client.token_expires_at = refresh_response["expires_at"]

    else:
        logging.info(
            "Token still valid, expires at {}".format(
                time.strftime(
                    "%a, %d %b %Y %H:%M:%S %Z",
                    time.localtime(access_token["expires_at"]),
                )
            )
        )

        client.access_token = access_token["access_token"]
        client.refresh_token = access_token["refresh_token"]
        client.token_expires_at = access_token["expires_at"]


def getMileagePerShoe(athlete):
    """Get show mileage from athlete object and return as dataframe"""

    mileage_per_shoe = []
    for shoe in athlete.to_dict()["shoes"]:
        mileage_per_shoe.append([shoe["name"], shoe["converted_distance"]])
    mileage_per_shoe_df = pd.DataFrame(
        mileage_per_shoe, columns=["name", "mileage"]
    )
    return mileage_per_shoe_df
