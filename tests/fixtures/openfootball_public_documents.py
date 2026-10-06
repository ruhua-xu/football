"""Pinned public README/CC0 text only, not historical match data.

Source: openfootball/football.json @ 40b3e1b7391932d133287115106304444bf297e1.
Embedded strings preserve the source bytes (including terminal spaces) while
keeping this Python fixture whitespace-clean. The test verifies both SHA256s.
"""

README = """## Frequently Asked Questions (& Answers)

Q: When (and how often) do the football.json datasets get updated?

A: The football.json datsets of the latest season (that is, 2026 & 2026/27)\x20
get auto-updated once a day (5 o'clock UTC) from the upstream Football.TXT datasets via a github action,
see the [action log @ yorobot/football.json](https://github.com/yorobot/football.json/actions) for details.

Note, however,  for the upstream Football.TXT datasets for now there's no automatic (daily) update.
See [Updates / Contributions Welcome - Please Update the Football.TXT Sources](#updates--contributions-welcome---please-update-the-footballtxt-sources) for details.



# football.json

Free open public domain football (match fixtures & results) data in the JSON (JavaScript Object Notation)
data interchange format.

Leagues include:

- English Premier League, Championship, League One, League Two
- Deutsche Bundesliga, 2. Bundesliga, 3. Liga
- Spanish Primera División ("La Liga"), Segunda División
- Italian Serie A, Serie B
- French Ligue 1, Ligue 2
- and much more

Example - Premier League 2015/16 Match Schedule (Fixtures and Results) - [`2015-16/en.1.json`](https://raw.githubusercontent.com/openfootball/football.json/master/2015-16/en.1.json):

``` json
{
  "name": "Premier League 2015/16",
  "matches": [
        {
          "round": "Matchday 1",
          "date":  "2015-08-08",
          "team1": "Manchester United",
          "team2": "Tottenham Hotspur",
          "score": { "ft": [1, 0] }
        },
        {
          "round": "Matchday 1",
          "date":  "2015-08-09",
          "team1": "Arsenal",
          "team2": "West Ham United",
          "score": { "ft": [0, 2] }
        },
        ...
  ]
}
```





## How to Use the Public JSON API Service - No API Key Required ;-)

Use the "raw" links served by GitHub (
otherwise you get the complete "formatted" GitHub page). Example:

```
$ curl https://raw.githubusercontent.com/openfootball/football.json/master/2015-16/en.1.json
```


## Updates / Contributions Welcome - Please Update the Football.TXT Sources

Note: The Football.JSON files get (auto-)generated using the datasets in the Football.TXT format, thus, **please do NOT
edit the (auto-)generated JSON files here but the Football.TXT sources upstream in the country repos** e.g.:

- English Premier League, Championship, League One, League Two in [**`/england`**](https://github.com/openfootball/england)  ([football.txt page index](https://openfootball.github.io/england/))
- Deutsche Bundesliga, 2. Bundesliga, 3. Liga in [**`/deutschland`**](https://github.com/openfootball/deutschland)  ([football.txt page index](https://openfootball.github.io/deutschland/))
- Spanish Primera División ("La Liga"), Segunda División in [**`/espana`**](https://github.com/openfootball/espana)   ([football.txt page index](https://openfootball.github.io/espana/))
- Italian Serie A, Serie B in [**`/italy`**](https://github.com/openfootball/italy)   ([football.txt page index](https://openfootball.github.io/italy/))
- French Ligue 1, Ligue 2 in [**`/france`**](https://github.com/openfootball/europe/tree/master/france) (in [`/europe`](https://github.com/openfootball/europe) ([football.txt page index](https://openfootball.github.io/europe/)))\x20\x20\x20
- and so on


and than wait to get the (auto-)generated football.json updates. If you only edit / patch the (auto-)generated JSON files here without updating
the sources upstream than your changes will get lost / overwritten with the next update.


## Do-It-Yourself (DIY) - How To (Auto)-Generate and Update the football.json Datasets

If you want to help out updating the (auto-)generated football.json datasets right here from the sources - you are more than welcome. See the [`yorobot/football.json`](https://github.com/yorobot/football.json) build scripts to get started
or use your very own.

 o o o

Or as a quick alternative\x20
you can use the [`fbtxt2json` command-line tool](https://github.com/sportdb/footty/tree/master/fbtxt2json) to convert any (match data) file in the Football.TXT format to JSON.\x20

Let's try to convert the English Premier League 2026/27
in the Football.TXT format (see [`england/2026-27/1-premierleague.txt`](https://github.com/openfootball/england/blob/master/2026-27/1-premierleague.txt)) to JSON:

```
$ fbtxt2json england/2026-27/1-premierleague.txt -o en.1.json
```

Tip - Or try to convert the complete [`/england`](https://github.com/openfootball/england) repo at once:

```
$ fbtxt2json . -o ./_site   # run inside /england; output json datasets to _site directory
```



## Add Your Leagues and Tournaments!

Any leagues or tournaments missing? Contributions welcome!
For starting your own repo from scratch see the [League Quick Starter Kit](https://github.com/openfootball/league-starter).


## More - Add Your Scripts Here

Enrique Lopez Magallon (@enadol) writes:

> Greetings! I started coding the following python robot to read the .txt files
> (for instance, "1-bundesliga-i.txt)" and generate an emulated version of the JSONs featured in football.json.
>
> https://github.com/enadol/fbjsonrobot
>
> Just make sure the proper .txt file is on the same folder, launch the file launch.py and that's (almost) it!
> For other leagues, adapt is required.
>
> It's still not perfect, but that's Github is for! 😄
>
> Have fun! ⚽️ ⚽️

Nurgazy Nazhimidinov (@nurgasemetey) writes:

> I use [the football.json datasets] in my tool.
>
> Basically it compares the last season and this season head-to-head results of the [English Premier League] team.
>
> Here is the link: https://compare-last-season.netlify.app/
>
> Here is the source code: https://github.com/nurgasemetey/compare-last-season

Rodolfo Melogli (@BusinessBloomer) writes:

> I use the openfootball Football.TXT fixture data for Serie A and the Premier League to power [fantatools.com](https://fantatools.com/en/), a set of free fantasy-football tools — fixture difficulty ratings and "next 5" run-in rankings for Fantacalcio and FPL.
>
> https://fantatools.com/en/


## License

The football.json schema, data and scripts are dedicated to the public domain. Use as you please with no restrictions whatsoever.



## Questions? Comments?

Yes, you can. More than welcome.
See [Help & Support »](https://github.com/openfootball/help)

"""

LICENSE = """CC0 1.0 Universal

Statement of Purpose

The laws of most jurisdictions throughout the world automatically confer
exclusive Copyright and Related Rights (defined below) upon the creator and
subsequent owner(s) (each and all, an "owner") of an original work of
authorship and/or a database (each, a "Work").

Certain owners wish to permanently relinquish those rights to a Work for the
purpose of contributing to a commons of creative, cultural and scientific
works ("Commons") that the public can reliably and without fear of later
claims of infringement build upon, modify, incorporate in other works, reuse
and redistribute as freely as possible in any form whatsoever and for any
purposes, including without limitation commercial purposes. These owners may
contribute to the Commons to promote the ideal of a free culture and the
further production of creative, cultural and scientific works, or to gain
reputation or greater distribution for their Work in part through the use and
efforts of others.

For these and/or other purposes and motivations, and without any expectation
of additional consideration or compensation, the person associating CC0 with a
Work (the "Affirmer"), to the extent that he or she is an owner of Copyright
and Related Rights in the Work, voluntarily elects to apply CC0 to the Work
and publicly distribute the Work under its terms, with knowledge of his or her
Copyright and Related Rights in the Work and the meaning and intended legal
effect of CC0 on those rights.

1. Copyright and Related Rights. A Work made available under CC0 may be
protected by copyright and related or neighboring rights ("Copyright and
Related Rights"). Copyright and Related Rights include, but are not limited
to, the following:

  i. the right to reproduce, adapt, distribute, perform, display, communicate,
  and translate a Work;

  ii. moral rights retained by the original author(s) and/or performer(s);

  iii. publicity and privacy rights pertaining to a person's image or likeness
  depicted in a Work;

  iv. rights protecting against unfair competition in regards to a Work,
  subject to the limitations in paragraph 4(a), below;

  v. rights protecting the extraction, dissemination, use and reuse of data in
  a Work;

  vi. database rights (such as those arising under Directive 96/9/EC of the
  European Parliament and of the Council of 11 March 1996 on the legal
  protection of databases, and under any national implementation thereof,
  including any amended or successor version of such directive); and

  vii. other similar, equivalent or corresponding rights throughout the world
  based on applicable law or treaty, and any national implementations thereof.

2. Waiver. To the greatest extent permitted by, but not in contravention of,
applicable law, Affirmer hereby overtly, fully, permanently, irrevocably and
unconditionally waives, abandons, and surrenders all of Affirmer's Copyright
and Related Rights and associated claims and causes of action, whether now
known or unknown (including existing as well as future claims and causes of
action), in the Work (i) in all territories worldwide, (ii) for the maximum
duration provided by applicable law or treaty (including future time
extensions), (iii) in any current or future medium and for any number of
copies, and (iv) for any purpose whatsoever, including without limitation
commercial, advertising or promotional purposes (the "Waiver"). Affirmer makes
the Waiver for the benefit of each member of the public at large and to the
detriment of Affirmer's heirs and successors, fully intending that such Waiver
shall not be subject to revocation, rescission, cancellation, termination, or
any other legal or equitable action to disrupt the quiet enjoyment of the Work
by the public as contemplated by Affirmer's express Statement of Purpose.

3. Public License Fallback. Should any part of the Waiver for any reason be
judged legally invalid or ineffective under applicable law, then the Waiver
shall be preserved to the maximum extent permitted taking into account
Affirmer's express Statement of Purpose. In addition, to the extent the Waiver
is so judged Affirmer hereby grants to each affected person a royalty-free,
non transferable, non sublicensable, non exclusive, irrevocable and
unconditional license to exercise Affirmer's Copyright and Related Rights in
the Work (i) in all territories worldwide, (ii) for the maximum duration
provided by applicable law or treaty (including future time extensions), (iii)
in any current or future medium and for any number of copies, and (iv) for any
purpose whatsoever, including without limitation commercial, advertising or
promotional purposes (the "License"). The License shall be deemed effective as
of the date CC0 was applied by Affirmer to the Work. Should any part of the
License for any reason be judged legally invalid or ineffective under
applicable law, such partial invalidity or ineffectiveness shall not
invalidate the remainder of the License, and in such case Affirmer hereby
affirms that he or she will not (i) exercise any of his or her remaining
Copyright and Related Rights in the Work or (ii) assert any associated claims
and causes of action with respect to the Work, in either case contrary to
Affirmer's express Statement of Purpose.

4. Limitations and Disclaimers.

  a. No trademark or patent rights held by Affirmer are waived, abandoned,
  surrendered, licensed or otherwise affected by this document.

  b. Affirmer offers the Work as-is and makes no representations or warranties
  of any kind concerning the Work, express, implied, statutory or otherwise,
  including without limitation warranties of title, merchantability, fitness
  for a particular purpose, non infringement, or the absence of latent or
  other defects, accuracy, or the present or absence of errors, whether or not
  discoverable, all to the greatest extent permissible under applicable law.

  c. Affirmer disclaims responsibility for clearing rights of other persons
  that may apply to the Work or any use thereof, including without limitation
  any person's Copyright and Related Rights in the Work. Further, Affirmer
  disclaims responsibility for obtaining any necessary consents, permissions
  or other rights required for any use of the Work.

  d. Affirmer understands and acknowledges that Creative Commons is not a
  party to this document and has no duty or obligation with respect to this
  CC0 or use of the Work.

For more information, please see
<http://creativecommons.org/publicdomain/zero/1.0/>
"""
