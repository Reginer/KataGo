#pragma once
#include "../NNUEglobal.h"
#include "../HashTable/VCFHashTable.h"
static_assert(NNUE::NU_LOC_NULL >= (MaxBS + 6)* (MaxBS + 6)+1||NNUE::NU_LOC_NULL<0);//��֤loc_null���������ϵĵ�

namespace VCF_NNUE {

  struct alignas(int64_t) PT
  {
    int16_t shapeDir;
    NU_Loc shapeLoc,loc1, loc2;
    PT():shapeDir(0), shapeLoc(NNUE::NU_LOC_NULL),loc1(NNUE::NU_LOC_NULL),loc2(NNUE::NU_LOC_NULL){}
    //PT(NU_Loc loc1,NU_Loc loc2):loc1(loc1),loc2(loc2){}
  };

  struct zobristTable
  {
    Hash128 boardhash[2][(MaxBS + 6) * (MaxBS + 6)];
    Hash128 isWhite;
    Hash128 basicRuleHash[3];
    zobristTable(int64_t seed);
  };

  enum PlayResult : int16_t {
    PR_Win,                  //˫�Ļ����������ץ��
    PR_OneFourWithThree,     //��������ͬʱ��������
    PR_OneFourWithTwo,     //��������ͬʱ�����߶�������������
    PR_OneFourWithoutTwo,  //��������ͬʱ�������߶�����
    PR_Lose                  //���Ϸ�(��ץ��)����û�����ɳ���
  };
  //��������NoThreeDecrease�֣����߶���NoTwoDecrease��
  //��ʼ����InitialBound��֮��ÿ��������BoundIncrease��
  static const int NormalDecrease = 1;
  static const int NoThreeDecrease = 5;
  static const int NoTwoDecrease = 20;

  //fullsearch�е�n������ʹ�õ�bound
  static inline int bound_n(int n)
  {
    if (n <= 2)return n;
    else return 2 + 6 * (n - 2);
  }
  //fullsearch�е�n������ʹ�õ�searchFactor
  static inline float searchFactor(int n)
  {
    if (n <= 2)return 10000;
    else return pow(0.6,n-2);
  }

  enum SearchResult : int16_t {
    SR_Win = -1,//�н�
    SR_Uncertain = 0,//��ȫ��֪����û�н⣬��û��
    SR_Lose = 32767//ȷ���޽�
    //����ֵ������ʱ�޽⣬��SearchResult=n��˵��������n-1�����������ĳ����޽⡱
  };


  inline NU_Loc xytoshapeloc(int x, int y) { return NU_Loc((MaxBS + 6) * (y + 3) + x + 3); }
  static const NU_Loc dirs[4] = { 1, MaxBS + 6, MaxBS + 6 + 1, -MaxBS - 6 + 1 };//+x +y +x+y +x-y
  //Ϊ�˷��㳣�����ã��ֿ���дһ��
  static const NU_Loc dir0 = 1;
  static const NU_Loc dir1 =  MaxBS + 6;
  static const NU_Loc dir2 =  MaxBS + 6 + 1;
  static const NU_Loc dir3 = -MaxBS - 6 + 1;
  static const int ArrSize = (MaxBS + 6) * (MaxBS + 6);//���Ƕ���3Ȧ������̸����



}  // namespace VCF

class VCFsolver_NNUE
{

  //hashTable
  static VCFHashTable_NNUE hashtable;
  static VCF_NNUE::zobristTable zobrist;


public:
  const int H, W;
  const bool isWhite;//����������Ǻ��壬��false���������ǰ�����true����true��colorȫ�Ƿ���
  const int basicRule;

  Color board[(MaxBS + 6) * (MaxBS + 6)];  //Ԥ��3Ȧ
  // shape=1*��������+8*����+64*�Է�����+512*���ֳ���+4096*����
  int16_t shape[4][(MaxBS + 6) * (MaxBS + 6)];  //Ԥ��3Ȧ
  Hash128 boardHash;

  int     ptNum;             // pts����ǰ���ٸ���Ч
  VCF_NNUE::PT pts[8 * MaxBS * MaxBS];  //����

  int64_t nodeNumThisSearch;//��¼��������Ѿ������˶��ٽڵ��ˣ�������ǰ��ֹ��ÿ��fullSearch��ʼʱ����
  int movenum;//�������ӿ�ʼvcf�����ڵľ����Ѿ���������
  NU_Loc PV[MaxBS * MaxBS];//��¼·��
  int PVlen;

  VCFsolver_NNUE() :VCFsolver_NNUE(C_BLACK,NNUE::DEFAULT_RULE) {}
  VCFsolver_NNUE(int basicRule,Color pla) : VCFsolver_NNUE(MaxBS, MaxBS, basicRule, pla) {}
  VCFsolver_NNUE(int h, int w, int basicRule, Color pla);
  void reset();

  //����board
  //b���ⲿ�����̣�pla�ǽ�����
  //katagoType�Ƿ���katago�����̣�false��Ӧloc=x+y*MaxBS��true��Ӧloc=x+1+(y+1)*(W+1)
  //colorType��Դ���̵ı�����ʽ��=false �����Է���=true ��ɫ��ɫ
  void setBoard(const Color* b, bool katagoType, bool colorType);


  VCF_NNUE::SearchResult fullSearch(float factor,int maxLayer, NU_Loc& bestmove, bool katagoType);//factor��������������֤factor�����ڽڵ�����
  inline int getPVlen() { return PVlen; };
  std::vector<NU_Loc> getPV();//�Ƚ���
  std::vector<NU_Loc> getPVreduced();//��������PV

  
  //�����ⲿ���ã��������̡���֤shape��ȷ������֤pts��ȷ��
  //locType=0 vcf�ڲ�loc��ʽ��=1 x+y*MaxBS��=2 katago��ʽ
  //colorType=false �����Է���=true ��ɫ��ɫ
  void playOutside(NU_Loc loc, Color color, int locType,bool colorType);
  void undoOutside(NU_Loc loc, int locType);//�����ⲿ���ã��������̡���֤shape��ȷ������֤pts��ȷ��

  //debug
  void printboard();

private:

  //����pts����������ǰһ�����������
  //ͬʱ��鼺�����Է���û���������
  VCF_NNUE::SearchResult resetPts(NU_Loc& onlyLoc);

  //�������յ�
  VCF_NNUE::PT findEmptyPoint2(NU_Loc loc,int dir);
  //��һ���յ�
  NU_Loc findEmptyPoint1(NU_Loc loc,NU_Loc bias);//bias=dirs[dir]

  //��һ���غϣ��ȳ����ٷ���
  //loc1�ǹ���loc2���أ�nextForceLoc�ǰ������
  VCF_NNUE::PlayResult playTwo(NU_Loc loc1,NU_Loc loc2, NU_Loc& nextForceLoc);
  void undo(NU_Loc loc);
  //maxNoThree�����maxNoThree��û���������������ַ��ĵĳ��Ĳ���
  //forceLoc����һ�������������Ϊ�����г���
  //winLoc�����ػ�ʤ��
  //ptNumOld����һ����������������֮��ԭ
  VCF_NNUE::SearchResult search(int maxNoThree, NU_Loc forceLoc);

  // shape=1*��������+8*����+64*�Է�����+512*���ֳ���+4096*����
  inline bool shape_isMyFive(int16_t s) { return (s & 0170777) == 5; }
  inline bool shape_isMyFour(int16_t s) { return (s & 0170777) == 4; }
  inline bool shape_isMyThree(int16_t s) { return (s & 0170777) == 3; }
  inline bool shape_isMyTwo(int16_t s) { return (s & 0170777) == 2; }
  inline bool shape_isOppFive(int16_t s) { return (s & 0177707) == 5*64; }
  inline bool shape_isOppFour(int16_t s) { return (s & 0177707) == 4*64; }



};