// Independent controller diagnostic, applied to an existing creator PMX slot.
float4x4 WVP:WORLDVIEWPROJECTION;
float4x4 Transform:CONTROLOBJECT <string name="Owner-A.x";>;
float3 Rotation:CONTROLOBJECT <string name="Owner-A.x";string item="Rxyz";>;
float RX:CONTROLOBJECT <string name="Owner-A.x";string item="Rx";>;
float RY:CONTROLOBJECT <string name="Owner-A.x";string item="Ry";>;
float RZ:CONTROLOBJECT <string name="Owner-A.x";string item="Rz";>;
float Opacity:CONTROLOBJECT <string name="Owner-A.x";string item="Tr";>;
float SelfTr:CONTROLOBJECT <string name="(self)";string item="Tr";> = .37;
struct O {
 float4 p:POSITION;
 float4 transformed:TEXCOORD0;
 float4 rotation_opacity:TEXCOORD1;
 float4 scalar_self:TEXCOORD2;
};
O VS(float4 p:POSITION) {
 O o;o.p=mul(p,WVP);
 o.transformed=mul(float4(.1,.2,.3,1),Transform);
 o.rotation_opacity=float4(Rotation,Opacity);
 o.scalar_self=float4(RX,RY,RZ,SelfTr);
 return o;
}
float4 PS(O i):COLOR {return .5+.01*(i.transformed+i.rotation_opacity+i.scalar_self);}
technique Main <string MMDPass="object";> {
 pass Draw {VertexShader=compile vs_3_0 VS();PixelShader=compile ps_3_0 PS();}
}
