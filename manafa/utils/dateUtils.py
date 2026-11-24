import os,re
import datetime, pytz,time



def convertToUnixTimestamp(batstattime):
	return batstattime	

def convertBatStatTimeToTimeStamp(batstattime,timezone="EST"):
	#secs=[86400,3600,60,1,0.001]
	secs = [0.001, 1, 60, 3600, 86400]
	val = 0.0
	ts = re.split(r'[a-z]+', batstattime.strip())
	ts.reverse()
	ts = filter(lambda x: x != '', ts)
	for i, x in enumerate(ts):
		#print(x)
		val += float(x) * secs[i]
		#print(val)
	return val
	#d1 =datetime.datetime.strptime(batstattime, '%Y-%m-%d-%H-%M-%S')
	#pst = pytz.timezone(timezone)
	#d= pst.localize(d1)
	#return time.mktime(d.timetuple())


def get_tzinfo(timezone):
	"""returns the tzinfo of a timezone given as IANA name (e.g. Europe/Lisbon) or as UTC offset (e.g. +0100).
	Returns None if the timezone is not known."""
	offset = re.match(r'^([+-])(\d{2}):?(\d{2})$', str(timezone).strip())
	if offset:
		delta = datetime.timedelta(hours=int(offset.group(2)), minutes=int(offset.group(3)))
		return datetime.timezone(-delta if offset.group(1) == '-' else delta)
	try:
		return pytz.timezone(str(timezone).strip())
	except pytz.UnknownTimeZoneError:
		return None


def is_known_timezone(timezone):
	return get_tzinfo(timezone) is not None


def local_time_to_timestamp(local_time, timezone):
	"""converts a naive datetime in the device timezone to a Unix timestamp (secs.ms)."""
	tz = get_tzinfo(timezone)
	if tz is None:
		raise ValueError("Unknown timezone '%s'. Use an IANA name (e.g. Europe/Lisbon) or a UTC offset (e.g. +0100)" % timezone)
	aware = tz.localize(local_time) if hasattr(tz, 'localize') else local_time.replace(tzinfo=tz)
	return aware.timestamp()


def convertBatStatTimeToTimeStampNew(batstattime, year, timezone="UTC"):
	# parse data like  11-11 14:49:47.952
	batstattime = str(year) + "-" + batstattime
	d1 = datetime.datetime.strptime(batstattime, '%Y-%m-%d %H:%M:%S.%f')
	return local_time_to_timestamp(d1, timezone)


def batStatResetTimeToTimeStamp(matime, timezone="UTC"):
	d1 = datetime.datetime.strptime(matime, '%Y-%m-%d-%H-%M-%S')
	return local_time_to_timestamp(d1, timezone)


def convertDateToTimeStamp(date, timezone="EST"):
	local = pytz.timezone(timezone)
	#time = time.mktime(datetime.datetime.strptime(s, "%d/%m/%Y").timetuple())
	return time
	#return None


def epochToDate(ts):
	return time.ctime(ts)


#x="2020-11-17-12-06-18"
#z = batStatResetTimeToTimeStamp(x)
#print(z)
#t2 = "1s394ms"
#t2 = "3d23h23m38s126ms"
#zz = convertBatStatTimeToTimeStamp(t2)
#print(zz)
#z = z+zz
#print(z)


