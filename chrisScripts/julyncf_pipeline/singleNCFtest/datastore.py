#!/usr/bin/env python
"""
Custom Datastore class that is utilized by Noisepy to store the preprocesed streams in RAM

Example structure: 

Preprocessed_data = {
    "XD.RUNG": {
        "2019-01-01": <obspy.Stream with BHZ, BHN, BHE traces>,
        "2019-01-02": <obspy.Stream ...>,
    },
    "XD.MTAN": { ... }
}


"""
from typing import Dict, List, Optional
from datetime import datetime, timezone, timedelta

import obspy
from datetimerange import DateTimeRange
from noisepy.seis.io.datatypes import (
    Channel, ChannelData, ChannelType, Station
)
from noisepy.seis.io.channelcatalog import XMLStationChannelCatalog


class InMemoryDataStore:

    def __init__(
        self,
        preprocessed_data: Dict[str, Dict[str, obspy.Stream]],
        catalog: XMLStationChannelCatalog,
        timespan_seconds: int = 86400,
        min_stations: int = 2,
    ) -> None:
        """
        Initializes the DataStore with preprocessed data and catalog information.
        """
        self.catalog = catalog
        self._channels: Dict[str, dict] = {}
        self._timespan_objects: Dict[str, DateTimeRange] = {}
        self._start_index: Dict[int, str] = {}

        #Populates the DataStore with preprocessed data
        for sta_id, dates in preprocessed_data.items():
            parts = sta_id.split(".")
            net, sta = parts[0], parts[1]
            #Iterates through the dates
            for date_str, stream in dates.items():
                # Converts the date string to a datetime object
                dt = datetime.fromisoformat(date_str).replace(tzinfo=timezone.utc)
                dr = DateTimeRange(dt, dt + timedelta(seconds=timespan_seconds))
                ts_str = str(dr) 
                if ts_str not in self._channels:
                    #Creates a new timespan if not already in the datastore
                    self._channels[ts_str] = {}
                    self._timespan_objects[ts_str] = dr
                    self._start_index[int(dt.timestamp())] = ts_str
                # Creates a new channel if not already in the datastore
                for tr in stream:
                    chan_code = tr.stats.channel
                    loc_code = tr.stats.location or ""
                    ch_key = (net, sta, chan_code, loc_code)
                    if ch_key not in self._channels[ts_str]:
                        ch_type = ChannelType(chan_code, loc_code)
                        station_obj = Station(net, sta, location=loc_code)
                        channel = Channel(ch_type, station_obj)
                        self._channels[ts_str][ch_key] = (channel, stream)
        
        # Enrich channels with catalog metadata and compute valid timespans  
        self._cached_channels: Dict[str, List[Channel]] = {}
        for ts_str, ch_dict in self._channels.items():
            dr = self._timespan_objects[ts_str]
            enriched: List[Channel] = []
            seen: set = set()
            for c, _ in ch_dict.values():
                try:
                    full_c = self.catalog.get_full_channel(dr, c)
                    if full_c.type.name != c.type.name:
                        full_c = c
                except Exception as e:
                    print(f"    Warning: catalog lookup failed for {c}: {e}")
                    full_c = c
                key = str(full_c)
                if key not in seen:
                    seen.add(key)
                    enriched.append(full_c)
            self._cached_channels[ts_str] = enriched

        self._valid_ts: List[str] = [
            ts for ts in sorted(self._channels.keys())
            if self._count_complete_stations(ts) >= min_stations
        ]
        n_total = len(self._channels)
        n_valid = len(self._valid_ts)
        total_ch = sum(len(v) for v in self._channels.values())
        print(
            f"  InMemoryDataStore: {total_ch} channel-streams across {n_valid}/{n_total} "
            f"day-timespans (filtered {n_total - n_valid} incomplete days)"
        )

    def _count_complete_stations(self, ts_str: str, min_channels: int = 3) -> int:
        """ counts how many stations have at least min_channels channels in this timespan"""
        station_channels: Dict[tuple, set] = {}
        for (net, sta, chan, loc) in self._channels[ts_str]:
            station_channels.setdefault((net, sta), set()).add(chan)
        return sum(1 for chans in station_channels.values() if len(chans) >= min_channels)

    def _resolve_ts(self, timespan: DateTimeRange) -> Optional[str]:
        """Given a DateTimeRange, return the canonical timespan key used in this DataStore."""
        ts_str = str(timespan)
        if ts_str in self._channels:
            return ts_str
        try:
            sd = timespan.start_datetime
            if sd.tzinfo is None:
                sd = sd.replace(tzinfo=timezone.utc)
            return self._start_index.get(int(sd.timestamp()))
        except Exception:
            return None

    def get_timespans(self) -> List[DateTimeRange]:
        """Returns the valid timespans in the DataStore"""
        return [self._timespan_objects[ts] for ts in self._valid_ts]

    def get_channels(self, timespan: DateTimeRange) -> List[Channel]:
        """Returns the channels for a given timespan."""
        ts_str = self._resolve_ts(timespan)
        if ts_str is None:
            return []
        return self._cached_channels.get(ts_str, [])

    def read_data(self, timespan: DateTimeRange, chan: Channel) -> ChannelData:
        """Reads the data for a given timespan and channel."""
        ts_str = self._resolve_ts(timespan)
        if ts_str is None:
            return ChannelData.empty()
        net = chan.station.network.strip()
        sta = chan.station.name.strip()
        loc = (getattr(chan.station, 'location', '') or '').strip()
        ch_key = (net, sta, chan.type.name, loc)
        # Gets the entry for the given timespan and channel
        entry = self._channels[ts_str].get(ch_key)
        if entry is None and loc != '':
            entry = self._channels[ts_str].get((net, sta, chan.type.name, ''))
        if entry is None:
            for stored_key, stored_entry in self._channels[ts_str].items():
                if (stored_key[0].strip() == net and
                        stored_key[1].strip() == sta and
                        stored_key[2] == chan.type.name):
                    entry = stored_entry
                    break
        if entry is None:
            return ChannelData.empty()
        _, stream = entry
        st = obspy.Stream(tr for tr in stream if tr.stats.channel == chan.type.name)
        if len(st) == 0:
            return ChannelData.empty()
        return ChannelData(st)

    def get_inventory(self, timespan: DateTimeRange, station: Station):
        """Gets the inventory for a given timespan and station."""
        return self.catalog.get_inventory(timespan, station)
