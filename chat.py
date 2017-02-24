import requests
import sys
import re
from base64 import b64encode


VERBOSE = False


def print_debug(*args):
    if VERBOSE:
        print(*args, file=sys.stderr)


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
    print_debug(repr(params))
    response = requests.get('http://nominatim.openstreetmap.org/search', params=params)
    print_debug(response.json())
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


def link_map(location, size=(600,400)):
    return "http://staticmap.openstreetmap.de/staticmap.php?center={lat},{lon}&zoom=18&size={size[0]}x{size[1]}&maptype=mapnik&markers={lat},{lon},lightblue1".format(**location, size=size)


def print_image(data, filename='unspecified.png'):
    sys.stdout.buffer.write(b"\033]1337;File=name=%s;size=%d;inline=1:%s\a\n" %
        (b64encode(filename.encode('UTF-8')), len(data), b64encode(data)))

def print_map(location):
    url = link_map(location)
    print_debug(url)
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
    def text(self, state):
        return "Waar ben je?"

    def interpret(self, answer, state):
        if re.match(r'.*\btrein\b.*', answer):
            state.on_the_road = True
            state.memory.add(equal(getter('class'), 'railway'))
            state.memory.add(equal(getter('type'), 'station'))
            match = re.match(r'.*\b(?:naar|richting)\s+(.+?)$', answer)
            if match:
                state.query.reset([match.group(1)])
        elif re.match(r'.*\bbus\b.*', answer):
            state.on_the_road = True
            # state.memory.add(equal(getter('type'), 'bus_stop')) # not supported now :(
        else:
            match = re.match(r'(?:Ik ben\s)?(?:(?:bij|in)\s(?:de\s)?)?(.+?)$', answer, flags=re.IGNORECASE)
            state.query.reset([match.group(1) if match is not None else answer])


class WaarGaJeNaarToe(Question):
    def text(self, state):
        return "Waar ga je naar toe?"

    def interpret(self, answer, state):
        match = re.match(r'^(?:Ik ga\s)?(?:(?:naar|richting)\s(?:de\s)?)?(.+?)$', answer, flags=re.IGNORECASE)
        state.query.reset([match.group(1) if match is not None else answer])


class IkWeetNietWaarJeBent(WaarBenJe):
    def text(self, state):
        return "Ik weet niet waar je {}.. kan je het nog een keer uitleggen?".format("naar toe gaat" if state.on_the_road else "bent")



class BenJeHier(Question):
    def __init__(self, location):
        self.location = location

    def text(self, state):
        if state.on_the_road:
            return "Ga je naar {}?".format(self.location['display_name'])
        else:
            return "Ben je op {}?".format(self.location['display_name'])

    def interpret(self, answer, state):
        if is_positive(answer):
            state.location = self.location
        else:
            state.memory.add(not_equal(getter('place_id'), self.location['place_id']))


class BedoelJeDieIn(Question):
    def __init__(self, feature, options):
        self.feature = feature
        self.options = options
        self.best = options[0]
        
    def text(self, state):
        return "In {}?".format(self.feature(self.best))

    def interpret(self, answer, state):
        if is_positive(answer):
            state.memory.add(equal(self.feature, self.feature(self.best)))
        else:
            match = re.match(r'(?:(?:nee|nope),?\s+)?(?:(?:die\s)?in\s+)?(.+?)$', answer.strip(), flags=re.IGNORECASE)
            if match is not None:
                state.query.enqueue(match.group(1))
            state.memory.add(not_equal(getter('place_id'), self.best['place_id']))
            state.memory.add(not_equal(self.feature, self.feature(self.best)))


class WelkeBedoelJe(Question):
    def __init__(self, options):
        self.options = options

    def text(self, state):
        return "Welke bedoel je?\n{}".format("\n".join(["{}. {}".format(n + 1, option['display_name']) for n, option in enumerate(self.options)]))

    def interpret(self, answer, state):
        if answer.isdigit() and int(answer) > 0 and int(answer) <= len(self.options):
            state.location = self.options[int(answer)]
        else:
            for option in self.options:
                state.memory.add(not_equal(getter('place_id'), option['place_id']))


class State(object):
    def __init__(self):
        self.query = Queue()
        self.memory = conjunction()
        self.options = None
        self.location = None
        self.on_the_road = None

    def next(self):
        if len(self.query) == 0:
            if self.on_the_road:
                return WaarGaJeNaarToe()
            else:
                return WaarBenJe()
        
        self.__update_options()

        if len(self.options) == 0:
            return IkWeetNietWaarJeBent()
        elif len(self.options) == 1:
            return BenJeHier(self.options[0])
        else:
            feature = self.__find_distinctive_feature(self.options)
            if feature is not None:
                return BedoelJeDieIn(feature, self.options)
            else:
                return WelkeBedoelJe(self.options)

    def __update_options(self):
        excluded_place_ids = [cond.value for cond in self.memory if isinstance(cond.key, getter) and cond.key.field == 'place_id']
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

    if '--verbose' in sys.argv:
        VERBOSE = True

    while state.location is None:
        question = state.next()
        print(question.text(state))
        question.interpret(input('> '), state)
        # print(repr(state.memory), file=sys.stderr)

    print_location(state.location)
    if state.on_the_road:
        print("Goede reis!")


if __name__ == '__main__':
    try:
        run()
    except EOFError:
        pass