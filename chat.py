import requests
import sys
import re
from base64 import b64encode


def ask_initial():
    print("Waar ben je?")
    return input("> ")


def search(query, excluded_place_ids=[]):
    params = {
        'q': query,
        'format': 'json',
        'countrycodes': 'nl',
        'namedetails': 1,
        'addressdetails': 1,
        'limit': 20,
        'exclude_place_ids': ",".join(excluded_place_ids),
        'email': 'jelmer.van.der.linde@rug.nl'
    }
    response = requests.get('http://nominatim.openstreetmap.org/search', params=params)
    print(response.json(), file=sys.stderr)
    return response.json()


class getter(object):
    def __init__(self, *field):
        self.field = field

    def __repr__(self):
        return '{}({})'.format(self.__class__.__name__, '.'.join(self.field))

    def __call__(self, val):
        for step in self.field:
            if step in val:
                val = val[step]
            else:
                return None
        return val


class multigetter(object):
    def __init__(self, getters):
        self.getters = getters

    def __repr__(self):
        return '{}({})'.format(self.__class__.__name__, ', '.join(map(repr, self.getters)))

    def __call__(self, val):
        for getter in self.getters:
            result = getter(val)
            if result is not None:
                return result


def distinct(values):
    prev = None
    for val in values:
        if prev is None:
            prev = val
        else:
            if prev != val:
                return True
    return False


def find_distinctive_feature(options):
    features = [
        Getter('address', 'city'),
        MutliGetter([
            Getter('address', 'pedestrian'),
            Getter('address', 'road')
        ])
    ]

    for feature in features:
        if distinct(map(feature, options)):
            return feature

    return None


def is_positive(answer):
    return re.match(r'(ja|yes|jup|yup|inderdaad|goed zo)\!*', answer.lower()) is not None


def get_information_from_answer(answer):
    match = re.match(r'(?:(?:nee|nope),?\s+)?(?:(?:die\s)?in\s+)?(.+?)$', answer, flags=re.IGNORECASE)
    return match.group(1) if match is not None else None


def human_join(items):
    if len(items) < 2:
        return items[0]
    else:
        return "{} en {}".format(", ".join(items[0:-1]), items[-1])


def link_osm(location):
    return "https://www.openstreetmap.org/node/{}".format(location['osm_id'])


def print_image(data, filename='unspecified.png'):
    sys.stdout.buffer.write(b"\033]1337;File=name=%s;size=%d;inline=1:%s\a\n" %
        (b64encode(filename.encode('UTF-8')), len(data), b64encode(data)))

def print_map(location):
    url = 'http://staticmap.openstreetmap.de/staticmap.php?center={lat},{lon}&zoom=18&size=865x512&maptype=mapnik&markers={lat},{lon},lightblue1'.format(**location)
    print(url)
    response = requests.get(url)
    print_image(response.content)

def print_location(location):
    print(location['display_name'])
    print(link_osm(location))
    print_map(location)


class Queue(object):
    def __init__(self, items = []):
        self.items = list(items);

    def __len__(self):
        return len(self.items)

    def __iter__(self):
        return self.items.__iter__()

    def reset(self, items):
        self.items = list(items)

    def enqueue(self, val):
        self.items.append(val)

    def dequeue(self):
        del self.items[0]


class equal(object):
    def __init__(self, key, value):
        self.key = key
        self.value = value

    def __repr__(self):
        return "{!r} is {!r}".format(self.key, self.value)

    def test(self, location):
        return self.key(location) == self.value


class not_equal(equal):
    def __repr__(self):
        return "{!r} is not {!r}".format(self.key, self.value)

    def test(self, location):
        return not super().test(location)


class conjunction(set):
    def test(self, location):
        for condition in self:
            if not condition.test(location):
                return False
        return True


class Question(object):
    pass


class WaarBenJe(Question):
    def text(self):
        return "Waar ben je?"

    def interpret(self, answer, state):
        match = re.match(r'^(?:Ik ben\s)?(?:bij\s(?:de\s)?)?(.+?)$', answer, flags=re.IGNORECASE)
        state.query.reset([match.group(1) if match is not None else answer])


class IkWeetNietWaarJeBent(WaarBenJe):
    def text(self):
        return "Ik weet niet waar je bent.. kan je het nog een keer uitleggen?"



class BenJeHier(Question):
    def __init__(self, location):
        self.location = location

    def text(self):
        return "Ben je op {}?".format(self.location['display_name'])

    def interpret(self, answer, state):
        if is_positive(answer):
            state.location = self.location
        else:
            if not re.match(r'^nee|nope$'): # is there more to this answer?
                match = re.match(r'(?:(?:nee|nope),?\s+)?(?:(?:die\s)?in\s+)?(.+?)$', answer, flags=re.IGNORECASE)
                if match is not None:
                    state.query.enqueue(match.group(1))
            state.memory.add(not_equal(getter('place_id'), self.location['place_id']))


class BedoelJeDieIn(Question):
    def __init__(self, feature, options):
        self.feature = feature
        self.options = options
        self.best = options[0]
        
    def text(self):
        return "In {}?".format(self.feature(self.best))

    def interpret(self, answer, state):
        if is_positive(answer):
            state.memory.add(equal(self.feature, self.feature(self.best)))
        else:
            state.memory.add(not_equal(self.feature, self.feature(self.best)))


class State(object):
    def __init__(self):
        self.query = Queue()
        self.memory = conjunction()
        self.options = None
        self.location = None

    def next(self):
        if len(self.query) == 0:
            return WaarBenJe()
        
        self.__update_options()

        if len(self.options) == 0:
            return IkWeetNietWaarJeBent()
        elif len(self.options) == 1:
            return BenJeHier(self.options[0])
        else:
            feature = self.__find_distinctive_feature(self.options)
            return BedoelJeDieIn(feature, self.options)

    def __update_options(self):
        excluded_place_ids = [cond.value for cond in self.memory if cond.key.field == 'place_id']
        options = search(", ".join(self.query), excluded_place_ids)
        self.options = [option for option in options if self.memory.test(option)]

    def __find_distinctive_feature(self, options):
        features = [
            getter('address', 'city'),
            multigetter([
                getter('address', 'pedestrian'),
                getter('address', 'road')
            ])
        ]

        for feature in features:
            if len(set(map(feature, options))) > 1:
                return feature

        return None



def run():
    state = State()

    while state.location is None:
        question = state.next()
        print(question.text())
        question.interpret(input('> '), state)
        print(repr(state.memory))

    print_location(state.location)


if __name__ == '__main__':
    try:
        run()
    except EOFError:
        pass