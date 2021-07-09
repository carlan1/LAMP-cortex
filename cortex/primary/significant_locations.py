from ..feature_types import primary_feature, log
from ..raw.gps import gps
from sklearn.cluster import KMeans
import pandas as pd
import numpy as np
import math
from sklearn.cluster import DBSCAN
import LAMP 


@primary_feature(
    name='cortex.significant_locations',
    dependencies=[gps],
    attach=False
)
def significant_locations(k_max=10, eps=1e-5, max_clusters=-1,
                          min_cluster_size=0.01, MAX_DIST=300, method='mode', **kwargs):
    """
    Get the coordinates of significant locations visited by the participant in
    the specified timeframe using the KMeans clustering method.
    NOTE: Via DBSCan, this algorithm first reduces the amount of gps readings
    used to generate significant locations. If there is a large amount of new
    gps data to reduce, this step can take a long time
    NOTE: DBScan uses O(n*k) memory. If you run it on a large GPS dataframe
    (>100k points), a memory crash could occur

    NOTE: This algorithm does NOT return the centroid radius and thus cannot
    be used to coalesce multiple SigLocs into one.

    :param k_max (int): The maximum KMeans clusters to test (FIXME).
    :param max_clusters (int): The number of clusters to create using
    ethod='mode'. Note: default is to use min_cluster_size when
    max_clusters=-1.
    :param min_cluster_size (float): The percentage of points that must be in
    a cluster for it to be significant.
    :param method: 'mode' or 'k_means'. Method for computing sig_locs.
    :return latitude (float): The latitude of the SigLoc centroid.
    :return longitude (float): The longitude of the SigLoc centroid.
    :return radius (float): The radius of the SigLoc centroid (in meters).
    :return proportion (float): The proportion of GPS events located within
    this centeroid compared to all GPS events over the entire time window.
    :return duration (int): The duration of time spent by the participant in
    the centroid.
    """
    if method == 'k_means':
        return _significant_locations_kmeans(k_max, eps, **kwargs)
    return _significant_locations_mode(max_clusters, min_cluster_size, MAX_DIST, **kwargs)

# Calculates straight-line (not great-circle) distance between two GPS points
# on Earth in kilometers; equivalent to roughly ~55% - 75% of the Haversian
# (great-circle) distance. 110.25 is conversion metric marking the length of a
# spherical degree.
#
# https://jonisalonen.com/2014/computing-distance-between-coordinates-can-be-simple-and-fast/
def euclid(g0, g1):
    def _euclid(lat, lng, lat0, lng0):  # degrees -> km
        return 110.25 * ((((lat - lat0) ** 2) + (((lng - lng0) * np.cos(lat0)) ** 2)) ** 0.5)
    return _euclid(g0[0], g0[1], g1[0], g1[1])

def distance(c1, c2):
    '''
    c1: (tuple) Geo coordinate
    c2: (tuple) Geo Coordinate
    return: distance in meters
    '''
    R = 6378.137
    dLat = c2[0] * math.pi / 180 - c1[0] * math.pi / 180
    dLon = c2[1] * math.pi / 180 - c1[1] * math.pi / 180
    a1 = math.sin(dLat / 2) * math.sin(dLat / 2)
    a2 = math.cos(c1[0] * math.pi / 180) * math.cos(c2[0] * math.pi / 180)
    a3 = math.sin(dLon / 2) * math.sin(dLon / 2)
    a = a1 + (a2 * a3)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    d = R * c
    return d * 1000

def remove_clusters(clusters, MAX_DIST):
    """ Function to remove clusters that are less than specified distance (MAX_DIST) away from at least one other cluster

        Args:
            clusters: (list of dicts) Each dict in the list is a significant location 
            MAX_DIST: (int) Maximum distance allowed between clusters (in meters)
    """
    n = len(clusters)
    clusters_removed = []
    for i in range(n - 1):
        big_cluster_prop = clusters[i]['proportion']
        if big_cluster_prop > 0:
            big_cluster_latlon = (clusters[i]['latitude'], clusters[i]['longitude'])
            for j in range(i + 1, n):
                small_cluster_latlon = (clusters[j]['latitude'], clusters[j]['longitude'])
                dist = distance(big_cluster_latlon, small_cluster_latlon)
                if dist < MAX_DIST:
                    clusters[i]['proportion'] += clusters[j]['proportion']
                    clusters[j]['proportion'] = 0
                    clusters[i]['duration'] += clusters[j]['duration']
        else:
            pass
    clusters = [cl for cl in clusters if cl['proportion'] > 0]
    for i in range(len(clusters)):
        clusters[i]['rank'] = i
    return clusters

# Helper to get location duration
def _location_duration(df, cluster):
    df = df[::-1].reset_index()
    arr_ext = np.r_[False, df['cluster'] == cluster, False]
    idx = np.flatnonzero(arr_ext[:-1] != arr_ext[1:])
    idx_list = list(zip(idx[:-1:2], idx[1::2] - int(True)))

    hometime_list = []
    for tup in idx_list:
        if tup[0] == tup[1]:
            continue
        duration = df['timestamp'][tup[1]] - df['timestamp'][tup[0]] 

        hometime_list.append(duration)

    return sum(hometime_list)

def _significant_locations_kmeans(k_max=10, eps=1e-5, **kwargs):
    #Get DB scan metadata fir
    try:
        reduced_data = LAMP.Type.get_attachment(kwargs['id'], 'cortex.significant_locations.reduced')['data']#['data']
    except:
        reduced_data = {'end':0, 'data':[]}

    reduced_data_end = reduced_data['end']
    new_reduced_data = reduced_data['data'].copy()

    if reduced_data_end < kwargs['end']: #update reduced data by getting new gps data and running dbscan
        ### DBSCAN ###
        _gps = gps(**{**kwargs, 'start':reduced_data_end})['data']
        df = pd.DataFrame.from_dict(_gps)
        df = df[df['timestamp'] != df['timestamp'].shift()]
        if len(df) == 0: return []

        #To prevent memory issues, limit size of db scan and perform iteratively
        cut_df = np.split(df, [30000*i for i in range(math.ceil(len(df) / 30000))])
        for d in cut_df:

            if len(d) == 0: continue

            d.reset_index(drop=True)
            new_reduced_data = reduced_data['data'].copy()
            dbscan = DBSCAN(eps=eps)
            props = dbscan.fit_predict(d[['latitude', 'longitude']].values)
            db_points = []
            for l in np.unique(props):
                db_lats, db_longs = [d.iloc[i]['latitude'] for i in range(len(d)) if props[i] == l], [d.iloc[i]['longitude'] for i in range(len(d)) if props[i] == l]
                db_duration = [d.iloc[i]['timestamp'] for i in range(len(d)) if props[i] == l]
                if l == -1:
                    db_points += [{'latitude':db_lats[i],
                    'longitude':db_longs[i],
                    'count':1} for i in range(len(db_lats))]
                else:
                    lat_mean, long_mean = np.mean(db_lats), np.mean(db_longs)
                    if len(reduced_data['data']) == 0:
                        db_points += [{'latitude':lat_mean, 
                                      'longitude':long_mean, 
                                  'count':len(db_lats)}]

                    else:
                        min_dist_index = np.argmin([euclid((loc['latitude'], loc['longitude']), (lat_mean, long_mean)) for loc in reduced_data['data']])
                        if euclid((reduced_data['data'][min_dist_index]['latitude'], reduced_data['data'][min_dist_index]['longitude']), 
                              (lat_mean, long_mean)) < 20:
                              new_reduced_data[min_dist_index]['count'] += len(db_lats)
                        else:
                            db_points += [{'latitude':lat_mean, 
                                          'longitude':long_mean, 
                                      'count':len(db_lats)}]

            #Add new db points
            new_reduced_data += db_points
            reduced_data = {'end':kwargs['end'], 'data':new_reduced_data}

        LAMP.Type.set_attachment(kwargs['id'], 'me', attachment_key='cortex.significant_locations.reduced', body=reduced_data)
       ### ###

    # Prepare input parameters.
    expanded_data = []
    for point in reduced_data['data']:
           expanded_data.extend([{'latitude':point['latitude'], 'longitude':point['longitude']} for _ in range(point['count'])])

    df = pd.DataFrame.from_dict(expanded_data)
    df2 = df[['latitude', 'longitude']].values
    K_clusters = range(1, min(k_max, len(df)))
    kmeans = [KMeans(n_clusters=i) for i in K_clusters]

    # Determine number of clusters to score.
    log.info(f'Calculating number of clusters to score with k_max={k_max}...')
    score = [kmeans[i].fit(df2).score(df2) for i in range(len(kmeans))]
    for i in range(len(score)):
        if i == len(score) - 1:
            k = i + 1
            break
        elif abs(score[i + 1] - score[i] < .01):
            k = i + 1
            break

    # Compute KMeans clusters. 
    log.info(f'Computing KMeans++ with k={k}...')
    kmeans = KMeans(n_clusters=k, init='k-means++')
    kmeans.fit(df2)

    #Get gps data for this window 
    _gps = gps(**kwargs)['data']
    if len(_gps) == 0: #return empty list if no data
        return [] 
    
    newdf = pd.DataFrame.from_dict(_gps)
    newdf_coords = newdf[['latitude', 'longitude']].values
    props = kmeans.predict(newdf_coords)
    newdf.loc[:, 'cluster'] = props


    # Add proportion of GPS within each centroid and return output.
    return [{
        'start':kwargs['start'], 
        'end':kwargs['end'],
        'latitude': center[0],
        'longitude': center[1],
        'rank': idx, #significant locations in terms of prevelance (0 being home)
        'radius': np.mean(euclid(center,

            # Transpose the points within the centroid and calculate the mean euclidean
            # distance (in km) from the center-point and convert that to meters.
            np.transpose(newdf_coords[np.argwhere(props == idx)].reshape((-1, 2)))
        ) * 1000) if props[props == idx].size > 0 else None,
        'proportion': props[props == idx].size / props.size,
        'duration': _location_duration(newdf, idx) #props[props == idx].size * 200 #EXPECTED duration in ms
    } for idx, center in enumerate(kmeans.cluster_centers_)]


def _significant_locations_mode(max_clusters, min_cluster_size, MAX_DIST, **kwargs):
    """ Function to assign points to k significant locations using mode method.

        Args:
            max_clusters: the maximum number of clusters to define
                          default (0) is to run until there are no clusters
                          left with greater than min_cluster_size points
                          ** set to -1 to use cluster_size only
            min_cluster_size: only applies if max_clusters is not set,
                          minumum number of points that can be classified
                          as a cluster, as a percentage of total number of
                          points
    """
    # get gps data
    _gps = gps(**kwargs)['data']
    if len(_gps) == 0:
        return []

    df = pd.DataFrame.from_dict(_gps)
    df = df[df['timestamp'] != df['timestamp'].shift()]
    df_clusters = df.copy(deep=True)
    ind = df.shape[0] * [-1]
    df_clusters["cluster"] = ind
    cluster_locs = []

    df_clusters['latitude'] = df_clusters['latitude'].apply(lambda x: round(x, 3))
    df_clusters['longitude'] = df_clusters['longitude'].apply(lambda x: round(x, 3))
    top_counts = df_clusters[['latitude', 'longitude']].value_counts()
    top_points = top_counts.index.tolist()

    min_cluster_points = int(min_cluster_size * len(df))

    if max_clusters != -1:
        for k in range(max_clusters):
            if k < len(top_points):
                df_clusters.loc[(df_clusters["latitude"] == top_points[k][0]) &
                                (df_clusters["longitude"] == top_points[k][1]),
                                'cluster'] = k
                cluster_locs.append(top_points[k])
    else:
        k = 0
        while k < len(top_counts) and top_counts.iloc[k] > min_cluster_points and k < len(df) - 1:
            df_clusters.loc[(df_clusters["latitude"] == top_points[k][0]) &
                            (df_clusters["longitude"] == top_points[k][1]),
                            'cluster'] = k
            cluster_locs.append(top_points[k])
            k += 1
            

    return remove_clusters([{
        'start':kwargs['start'],
        'end':kwargs['end'],
        'latitude': center[0],
        'longitude': center[1],
        'rank': idx,  # significant locations in terms of prevelance (0 being home)
        'radius': np.mean(euclid((center[1],center[0]),

            # Transpose the points within the centroid and calculate the mean
            # euclidean distance (in km) from the center-point and convert that
            # to meters.
            np.transpose(df.loc[(df_clusters['cluster'] == idx),['longitude','latitude']].values.reshape((-1, 2)))
        ) * 1000) if df_clusters[df_clusters['cluster'] != idx].size else None,
        'proportion': df_clusters[df_clusters['cluster'] == idx].size / df_clusters[df_clusters['cluster'] != -1].size,
        'duration': _location_duration(df_clusters, idx)
    } for idx, center in enumerate(cluster_locs)], MAX_DIST)